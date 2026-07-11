import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeployContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requirements = (ROOT / "requirements.txt").read_text()
        cls.update = (ROOT / "deploy/update.sh").read_text()
        cls.service = (ROOT / "deploy/pinhaoke.service").read_text()
        cls.nginx = (ROOT / "deploy/nginx.conf").read_text()
        readme = ROOT / "deploy/README.md"
        cls.deploy_readme = readme.read_text() if readme.exists() else ""

    def test_direct_dependencies_are_exactly_pinned_to_audited_versions(self):
        self.assertEqual(
            self.requirements.splitlines(),
            [
                "fastapi==0.136.1",
                "starlette==1.3.1",
                "uvicorn[standard]==0.44.0",
            ],
        )

    def test_update_is_strict_root_only_and_nonblocking(self):
        self.assertRegex(self.update, r"set -[^\n]*e[^\n]*u[^\n]*o pipefail")
        self.assertIn('EUID', self.update)
        self.assertIn('flock -n', self.update)
        self.assertLess(self.update.index('flock -n'), self.update.index('git fetch'))
        required_tools = {
            "awk",
            "chmod",
            "chown",
            "curl",
            "find",
            "git",
            "grep",
            "install",
            "journalctl",
            "mktemp",
            "mv",
            "python3",
            "rm",
            "sha256sum",
            "sleep",
            "systemctl",
        }
        tool_loop = re.search(r"for tool in ([^;]+); do", self.update)
        self.assertIsNotNone(tool_loop)
        self.assertTrue(required_tools.issubset(set(tool_loop.group(1).split())))
        self.assertIn('command -v "$tool"', self.update)

    def test_update_resolves_and_prefetches_exact_origin_main_before_stop(self):
        stop = self.update.index('systemctl stop "$SERVICE"')
        for text in (
            'git fetch --prune origin "+refs/heads/main:refs/remotes/origin/main"',
            'git rev-parse --verify "refs/remotes/origin/main^{commit}"',
            'git show "$TARGET_COMMIT:.gitattributes"',
            'command -v git-lfs',
            'git lfs fetch origin "$TARGET_COMMIT"',
            'git lfs fsck --objects "$TARGET_COMMIT"',
        ):
            self.assertIn(text, self.update)
            self.assertLess(self.update.index(text), stop)
        self.assertIn('git reset --hard "$TARGET_COMMIT"', self.update)

    def test_update_builds_verified_candidate_from_target_requirements_before_stop(self):
        stop = self.update.index('systemctl stop "$SERVICE"')
        for text in (
            'git show "$TARGET_COMMIT:requirements.txt"',
            'sha256sum "$TARGET_REQUIREMENTS"',
            'python3 -m venv "$CANDIDATE_VENV"',
            '"$CANDIDATE_VENV/bin/pip" install -r "$TARGET_REQUIREMENTS"',
            '"$CANDIDATE_VENV/bin/pip" check',
            '"$CANDIDATE_VENV/bin/python"',
        ):
            self.assertIn(text, self.update)
            self.assertLess(self.update.index(text), stop)
        self.assertIn('.requirements.sha256', self.update)
        self.assertNotIn('rm -rf venv', self.update)

    def test_update_swaps_on_same_filesystem_and_rolls_back_failed_rename(self):
        self.assertIn('mktemp -d "$APP_DIR/.deploy-stage.', self.update)
        self.assertIn('mv "$LIVE_VENV" "$BACKUP_VENV"', self.update)
        self.assertIn('mv "$CANDIDATE_VENV" "$LIVE_VENV"', self.update)
        self.assertGreaterEqual(self.update.count('mv "$BACKUP_VENV" "$LIVE_VENV"'), 1)

    def test_update_stops_fail_fast_then_installs_and_starts_service(self):
        stop = self.update.index('systemctl stop "$SERVICE"')
        reset = self.update.index('git reset --hard "$TARGET_COMMIT"')
        unit = self.update.index('deploy/pinhaoke.service')
        reload_service = self.update.index('systemctl daemon-reload')
        start = self.update.index('systemctl start "$SERVICE"')
        self.assertLess(stop, reset)
        self.assertLess(reset, unit)
        self.assertLess(unit, reload_service)
        self.assertLess(reload_service, start)
        self.assertNotIn('systemctl stop "$SERVICE" ||', self.update)
        self.assertNotIn('chown -R', self.update)
        self.assertIn('find "$APP_DIR" -xdev', self.update)
        self.assertIn('chown root:www-data', self.update)

    def test_update_materializes_lfs_and_sets_read_only_service_permissions(self):
        self.assertIn('git lfs checkout', self.update)
        self.assertGreaterEqual(self.update.count('git lfs fsck --objects "$TARGET_COMMIT"'), 2)
        self.assertIn('chmod 0750', self.update)
        self.assertIn('chmod 0640', self.update)
        self.assertIn('"$LIVE_VENV/bin"', self.update)
        self.assertIn('"$APP_DIR/Images"', self.update)
        self.assertIn('"$APP_DIR/数据库"', self.update)

    def test_update_smoke_tests_all_three_api_contracts_with_bounded_retries(self):
        for endpoint in (
            "/api/health",
            "/api/filters?term=fall",
            "/api/courses?term=fall&page_size=1",
        ):
            self.assertIn(endpoint, self.update)
        self.assertIn(
            'for ((attempt = 1; attempt <= SMOKE_ATTEMPTS; attempt++)); do',
            self.update,
        )
        self.assertNotIn("$(seq", self.update)
        self.assertIn('curl --silent --show-error --max-time', self.update)
        for invariant in ('"status"', '"databases"', '4421', '"courses"'):
            self.assertIn(invariant, self.update)
        self.assertIn('journalctl -u "$SERVICE"', self.update)
        self.assertIn('systemctl is-active --quiet "$SERVICE"', self.update)

    def test_service_runs_as_www_data_with_read_only_sandbox(self):
        for setting in (
            "User=www-data",
            "Group=www-data",
            "Environment=PYTHONDONTWRITEBYTECODE=1",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "ProtectKernelTunables=true",
            "ProtectKernelModules=true",
            "ProtectControlGroups=true",
            "RestrictSUIDSGID=true",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
            "UMask=0027",
            "ReadOnlyPaths=/opt/pinhaoke",
        ):
            self.assertIn(setting, self.service)
        self.assertNotIn("ReadWritePaths=/opt/pinhaoke", self.service)
        self.assertIn("127.0.0.1", self.service)

    def test_nginx_has_canonical_https_and_exact_certbot_paths(self):
        self.assertRegex(self.nginx, r"listen 80;")
        self.assertRegex(self.nginx, r"return 30[18] https://www\.pinhaoke\.love\$request_uri;")
        self.assertIn("listen 443 ssl", self.nginx)
        self.assertIn(
            "ssl_certificate /etc/letsencrypt/live/pinhaoke.love/fullchain.pem;",
            self.nginx,
        )
        self.assertIn(
            "ssl_certificate_key /etc/letsencrypt/live/pinhaoke.love/privkey.pem;",
            self.nginx,
        )

    def test_nginx_images_cache_and_gzip_are_safe(self):
        self.assertIn("alias /opt/pinhaoke/Images/;", self.nginx)
        self.assertIn('Cache-Control "public, max-age=2592000"', self.nginx)
        self.assertNotIn("immutable", self.nginx.lower())
        gzip_types = re.search(r"gzip_types\s+([^;]+);", self.nginx, re.DOTALL)
        self.assertIsNotNone(gzip_types)
        self.assertNotIn("text/html", gzip_types.group(1).split())

    def test_nginx_proxies_scheme_and_sets_security_headers(self):
        for setting in (
            "proxy_set_header X-Forwarded-Proto $scheme;",
            "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            'X-Content-Type-Options "nosniff"',
            'Referrer-Policy "strict-origin-when-cross-origin"',
            'X-Frame-Options "SAMEORIGIN"',
            'Strict-Transport-Security "max-age=',
        ):
            self.assertIn(setting, self.nginx)

    def test_deploy_readme_keeps_nginx_manual_and_documents_rollback(self):
        for text in (
            "nginx -t",
            "Certbot",
            "手动",
            "回滚",
            "deploy/update.sh",
            "不会",
        ):
            self.assertIn(text, self.deploy_readme)
        self.assertRegex(self.deploy_readme, r"systemctl\s+(status|is-active)")


if __name__ == "__main__":
    unittest.main()
