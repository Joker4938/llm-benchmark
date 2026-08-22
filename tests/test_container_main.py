import os
import unittest
from unittest.mock import patch

from benchmark_server import container_main


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class ContainerMainTests(unittest.TestCase):
    def test_build_commands_uses_configured_host_and_port(self):
        with patch.dict(
            os.environ,
            {
                'LLM_BENCHMARK_HOST': '127.0.0.1',
                'LLM_BENCHMARK_PORT': '9000',
            },
        ):
            commands = container_main.build_commands()

        self.assertEqual(
            [container_main.sys.executable, '-m', 'benchmark_server.executor_main'],
            commands[0],
        )
        self.assertEqual(
            [
                container_main.sys.executable,
                '-m',
                'uvicorn',
                'benchmark_server.main:app',
                '--host',
                '127.0.0.1',
                '--port',
                '9000',
            ],
            commands[1],
        )

    def test_supervisor_stops_peer_when_one_child_exits(self):
        failed_executor = FakeProcess(returncode=7)
        running_api = FakeProcess()
        commands = [['executor'], ['api']]

        with patch(
            'benchmark_server.container_main.subprocess.Popen',
            side_effect=[failed_executor, running_api],
        ) as popen:
            code = container_main.run_supervisor(commands, grace_seconds=0)

        self.assertEqual(7, code)
        self.assertTrue(running_api.terminated)
        self.assertFalse(running_api.killed)
        self.assertEqual(
            [(["executor"],), (["api"],)],
            [call.args for call in popen.call_args_list],
        )


if __name__ == '__main__':
    unittest.main()
