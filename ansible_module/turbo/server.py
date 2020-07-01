import argparse
import asyncio
import importlib
import inspect
import json
import collections
import os
import signal
import sys
import traceback


from ansible_module.turbo.exceptions import EmbeddedModuleFailure
from ansible_module.turbo.exceptions import EmbeddedModuleSuccess

sys_path_lock = asyncio.Lock()


def fork_process():
    """
    This function performs the double fork process to detach from the
    parent process and execute.
    """
    pid = os.fork()

    if pid == 0:
        # Set stdin/stdout/stderr to /dev/null
        fd = os.open(os.devnull, os.O_RDWR)

        # clone stdin/out/err
        for num in range(3):
            if fd != num:
                os.dup2(fd, num)

        # close otherwise
        if fd not in range(3):
            os.close(fd)

        # Make us a daemon
        pid = os.fork()

        # end if not in child
        if pid > 0:
            os._exit(0)

        # get new process session and detach
        sid = os.setsid()
        if sid == -1:
            raise Exception("Unable to detach session while daemonizing")

        # avoid possible problems with cwd being removed
        os.chdir("/")

        pid = os.fork()
        if pid > 0:
            os._exit(0)
    else:
        exit(0)
    return pid


class EmbeddedModule:
    def __init__(self, module_name, collection_name, ansiblez_path, check_mode, params):
        self.module_name = module_name
        self.collection_name = collection_name
        self.ansiblez_path = ansiblez_path
        self.check_mode = check_mode
        self.params = params
        self.init_class = None
        self.module_class = None
        self.module_path = "ansible_collections.{collection_name}.plugins.modules.{module_name}".format(
            collection_name=collection_name, module_name=module_name
        )
        self.init_path = "ansible_collections.{collection_name}.plugins.module_utils.init".format(
            collection_name=collection_name
        )
        self._signature_hash_cache = None
        self._initialized_env = None

    async def load(self):
        import sys

        async with sys_path_lock:
            sys.path.insert(0, self.ansiblez_path)
            self.module_class = importlib.import_module(self.module_path)
            self.init_class = importlib.import_module(self.init_path)
            if not hasattr(self.init_class, "initialize"):
                raise EmbeddedModuleFailure("No initialize function found!")
        self.initialize_params = self.init_class.initialize_params

    async def unload(self):
        async with sys_path_lock:
            sys.path = [i for i in sys.path if i != self.ansiblez_path]
            for path, module in tuple(sys.modules.items()):
                if not path or not module:
                    continue
                if path.startswith("ansible_collections"):
                    del sys.modules[path]
            importlib.invalidate_caches()
            sys.path_importer_cache.clear()

    def init_params(self):
        return {
            k: v
            for k, v in self.params.items()
            if k in self.init_class.initialize_params
        }

    def signature_hash(self):
        if not self._signature_hash_cache:
            json_data = json.dumps(self.init_params(), sort_keys=True)
            self._signature_hash_cache = hash(json_data)
        return self._signature_hash_cache

    async def initialize(self, sessions):
        if not self.signature_hash() in sessions[self.collection_name]:
            try:
                if inspect.iscoroutinefunction(self.init_class.initialize):
                    sessions[self.collection_name][
                        self.signature_hash()
                    ] = await self.init_class.initialize(**self.init_params())
                else:
                    sessions[self.collection_name][
                        self.signature_hash()
                    ] = self.init_class.initialize(**self.init_params())
            except EmbeddedModuleFailure as e:
                raise e
            except Exception as e:
                raise EmbeddedModuleFailure(e)
        self._initialized_env = sessions[self.collection_name][self.signature_hash()]

    async def run(self):
        if not hasattr(self.module_class, "entry_point"):
            raise EmbeddedModuleFailure("No entry_point found!")
        try:
            if inspect.iscoroutinefunction(self.module_class.entry_point):
                result = await self.module_class.entry_point(
                    self, **self._initialized_env
                )
            else:
                result = self.module_class.entry_point(self, **self._initialized_env)
        except EmbeddedModuleSuccess:
            raise
        except Exception as e:
            raise EmbeddedModuleFailure(e)
        if not result:
            result = {}
        return result

    def exit_json(self, **kwargs):
        raise EmbeddedModuleSuccess(**kwargs)


class AnsibleVMwareTurboMode:
    def __init__(self):
        self.sessions = collections.defaultdict(dict)
        self.socket_path = None
        self.ttl = None

    async def ghost_killer(self):
        await asyncio.sleep(self.ttl)
        self.stop()

    async def handle(self, reader, writer):
        self._watcher.cancel()
        self._watcher = self.loop.create_task(self.ghost_killer())

        raw_data = await reader.read(1024 * 10)
        if not raw_data:
            return
        try:
            (
                module_name,
                collection_name,
                ansiblez_path,
                check_mode,
                params,
            ) = json.loads(raw_data)
        except json.decoder.JSONDecodeError as e:
            return

        embedded_module = EmbeddedModule(
            module_name, collection_name, ansiblez_path, check_mode, params
        )

        await embedded_module.load()
        try:
            await embedded_module.initialize(self.sessions)
            result = await embedded_module.run()
        except EmbeddedModuleSuccess as e:
            result = e.kwargs
        except EmbeddedModuleFailure as e:
            # result = {"msg": e.get_message(), "failed": True}
            result = {"msg": traceback.format_stack() + [str(e)], "failed": True}
        except Exception as e:
            result = {"msg": traceback.format_stack() + [str(e)], "failed": True}

        writer.write(json.dumps(result).encode())
        writer.close()

        await embedded_module.unload()

    def start(self):
        self.loop = asyncio.get_event_loop()
        self.loop.add_signal_handler(signal.SIGTERM, self.stop)
        self._watcher = self.loop.create_task(self.ghost_killer())

        self.loop.create_task(
            asyncio.start_unix_server(
                self.handle, path=self.socket_path, loop=self.loop
            )
        )
        self.loop.run_forever()

    def stop(self):
        os.unlink(self.socket_path)
        self.loop.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start a background daemon.")
    parser.add_argument(
        "--socket-path", default=os.environ["HOME"] + "/.ansible/turbo_mode.socket"
    )
    parser.add_argument("--ttl", default=15, type=int)
    parser.add_argument("--fork", action="store_true")

    args = parser.parse_args()
    if args.fork:
        fork_process()

    server = AnsibleVMwareTurboMode()
    server.socket_path = args.socket_path
    server.ttl = args.ttl
    server.start()
