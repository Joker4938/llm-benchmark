from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeploymentAssetTests(unittest.TestCase):
    def test_update_restore_reapplies_non_root_runtime_ownership(self):
        script = (PROJECT_ROOT / 'update.sh').read_text(encoding='utf-8')

        self.assertIn('RESTORE_UID=10001', script)
        self.assertIn('RESTORE_GID=10001', script)
        self.assertIn('--env "RESTORE_UID=$RESTORE_UID"', script)
        self.assertIn('--env "RESTORE_GID=$RESTORE_GID"', script)
        self.assertIn('os.chown(path, uid, gid, follow_symlinks=False)', script)


if __name__ == '__main__':
    unittest.main()
