import socket
import os
import sys

import json
import time

from ansible.module_utils.basic import AnsibleModule


class AnsibleTurboModule(AnsibleModule):

    def __init__(self, module_name, collection_name, **kwargs):
        super().__init__(**kwargs)
        self.module_name = module_name
        self.collection_name = collection_name
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket_path = os.environ['HOME'] + '/.ansible/turbo_mode.socket'
        self._running = None

    def start_daemon(self):
        import subprocess
        import sys
        if self._running:
            return
        p = subprocess.Popen([sys.executable, '-m', 'ansible_module.turbo.server', '--fork', '--socket-path', self._socket_path], close_fds=True)
        self._running = True
        p.communicate()
        return


    def connect(self):
        for attempt in range(100, -1, -1):
            try:
                self._socket.connect(self._socket_path)
                return
            except (ConnectionRefusedError, FileNotFoundError) as e:
                self.start_daemon()
                if attempt == 0:
                    raise
            time.sleep(0.01)


    def run(self):
        self.connect()
        result = dict(changed=False, original_message='', message='')
        ansiblez_path = sys.path[0]
        data = [self.module_name, self.collection_name, ansiblez_path, self.check_mode, self.params]
        self._socket.send(json.dumps(data).encode())
        raw = self._socket.recv((1024 * 10)).decode()
        self._socket.close()
        result = json.loads(raw)
        self.exit_json(**result)



