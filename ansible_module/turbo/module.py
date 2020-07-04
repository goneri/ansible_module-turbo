import json
import os
import socket
import sys
import time

import ansible.module_utils.basic
from ansible_module.turbo.exceptions import EmbeddedModuleSuccess


class AnsibleTurboModule(ansible.module_utils.basic.AnsibleModule):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._socket = None
        self._socket_path = os.environ["HOME"] + "/.ansible/turbo_mode.socket"
        self._running = None
        self.run_on_daemon()

    def start_daemon(self):
        import subprocess

        if self._running:
            return
        p = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ansible_module.turbo.server",
                "--fork",
                "--socket-path",
                self._socket_path,
            ],
            close_fds=True,
        )
        self._running = True
        p.communicate()
        return

    def connect(self):
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for attempt in range(100, -1, -1):
            try:
                self._socket.connect(self._socket_path)
                return
            except (ConnectionRefusedError, FileNotFoundError) as e:
                self.start_daemon()
                if attempt == 0:
                    raise
            time.sleep(0.01)

    def run_on_daemon(self):
        if sys.argv[0].endswith("/server.py"):
            return
        self.connect()
        result = dict(changed=False, original_message="", message="")
        ansiblez_path = sys.path[0]
        data = [
            ansiblez_path,
            ansible.module_utils.basic._ANSIBLE_ARGS.decode(),
        ]
        self._socket.send(json.dumps(data).encode())
        b = self._socket.recv((1024 * 10))
        raw = b.decode()
        self._socket.close()
        result = json.loads(raw)
        self.exit_json(**result)

    def exit_json(self, **kwargs):
        if not sys.argv[0].endswith("/server.py"):
            super().exit_json(**kwargs)
        else:
            self.do_cleanup_files()
            raise EmbeddedModuleSuccess(**kwargs)
