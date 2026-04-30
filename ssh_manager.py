import os
import paramiko
import base64
import hashlib
import re
import io
import logging
import shlex
import time
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger('discobunty.ssh')
SUDO_PROMPT_PATTERN = re.compile(r'(?m)^.*\[sudo\] password for.*$\n?|^\s*Password:\s*$\n?')




def _humanize_age_seconds(raw_value: str) -> str:
    raw_value = (raw_value or "").strip()
    if not raw_value or raw_value == "n/a":
        return "n/a"
    if raw_value.endswith("s"):
        raw_value = raw_value[:-1]
    try:
        total_seconds = int(float(raw_value))
    except ValueError:
        return raw_value

    if total_seconds < 60:
        return f"{total_seconds}s"

    units = (
        ("d", 86400),
        ("h", 3600),
        ("m", 60),
    )
    parts = []
    remainder = total_seconds
    for suffix, size in units:
        if remainder >= size:
            value = remainder // size
            remainder %= size
            parts.append(f"{value}{suffix}")
        if len(parts) == 2:
            break
    if not parts:
        parts.append(f"{remainder}s")
    return " ".join(parts)


class _FingerprintCapturePolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self):
        self.key = None
        self.fingerprint = None
    def missing_host_key(self, client, hostname, key):
        self.key = key
        # Standard OpenSSH SHA256 format
        self.fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode('utf-8').replace('=', '')
        raise paramiko.SSHException(f"Host key verification failed for {hostname}")

class SSHManager:
    def __init__(self, servers: List[Dict]):
        # Now initialized with the list from ConfigManager
        self.servers = servers
        self.servers_by_alias = {s['alias']: s for s in servers}
        self._log_cache = {} # Cache for log file lists: {alias: (timestamp, [files])}

    def get_server_aliases(self) -> List[str]:
        """Return a list of all server aliases for autocomplete."""
        return [s['alias'] for s in self.servers]

    def get_server_by_alias(self, alias: str) -> Optional[Dict]:
        """Find a server configuration by its alias."""
        return self.servers_by_alias.get(alias)

    def _configure_host_keys(self, client: paramiko.SSHClient, host: str, port: int, trust_host: bool) -> Tuple[bool, Optional[str], _FingerprintCapturePolicy]:
        """Configure known_hosts and missing host key policy."""
        known_hosts_path = os.getenv('KNOWN_HOSTS_FILE', os.path.join(os.getenv('DATA_DIR', '/app/data'), 'known_hosts'))
        capture_policy = _FingerprintCapturePolicy()
        
        if trust_host:
            os.makedirs(os.path.dirname(known_hosts_path), exist_ok=True)

        if os.path.exists(known_hosts_path):
            try:
                client.load_host_keys(known_hosts_path)
                host_keys = client.get_host_keys()
                host_str = f"[{host}]:{port}" if port != 22 else host
                
                if trust_host:
                    # Security: If key already exists but is different, block AutoAddPolicy
                    if host_str in host_keys or host in host_keys:
                        client.set_missing_host_key_policy(paramiko.RejectPolicy())
                    else:
                        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                else:
                    client.set_missing_host_key_policy(capture_policy)
                
                logger.info(f"Loaded known_hosts from {known_hosts_path}")
            except Exception as e:
                logger.error(f"Failed to load known_hosts: {e}")
                return False, f"Error: Failed to load SSH known_hosts from {known_hosts_path}.", capture_policy
        else:
            if trust_host:
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            else:
                client.set_missing_host_key_policy(capture_policy)

        return True, known_hosts_path, capture_policy

    def _connect_client(self, client: paramiko.SSHClient, config: Dict, host: str, port: int, user: str) -> Optional[str]:
        """Handle SSH authentication and connection."""
        auth_method = config.get('auth_method', 'key')

        if auth_method == 'key':
            key_value = config.get('key')
            if not key_value:
                return "Error: SSH Key not provided."

            if key_value.startswith('/') or (os.path.exists(key_value) and os.path.isfile(key_value)):
                client.connect(hostname=host, port=port, username=user, key_filename=key_value, timeout=10)
            else:
                private_key = None
                for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey]:
                    try:
                        private_key = key_class.from_private_key(io.StringIO(key_value))
                        if private_key: break
                    except Exception: continue

                if not private_key:
                    return "Error: Could not parse SSH key string."

                client.connect(hostname=host, port=port, username=user, pkey=private_key, timeout=10)
        else:
            password = config.get('password')
            if not password:
                return "Error: Password not provided."
            client.connect(hostname=host, port=port, username=user, password=password, timeout=10)

        return None

    def _get_ssh_client(self, config: Dict, trust_host: bool = False) -> Tuple[Optional[paramiko.SSHClient], Optional[str], Optional[str]]:
        """
        Internal helper to create and connect an SSH client.
        Returns (client, error_message, fingerprint).
        """
        if not config:
            return None, "Error: Invalid server configuration.", None

        host = config.get('host')
        user = config.get('user', 'root')
        port = int(config.get('port', 22))

        client = paramiko.SSHClient()

        # Configure host keys
        success, host_keys_info, capture_policy = self._configure_host_keys(client, host, port, trust_host)
        if not success:
            client.close()
            return None, host_keys_info, None

        known_hosts_path = host_keys_info

        try:
            # Connect
            err = self._connect_client(client, config, host, port, user)
            if err:
                client.close()
                return None, err, None
            
            # Save host keys if trusted
            if trust_host:
                try:
                    client.save_host_keys(known_hosts_path)
                    logger.info(f"Successfully added and saved host key for {host} to {known_hosts_path}")
                except Exception as save_err:
                    err_msg = f"Failed to save host keys to {known_hosts_path}: {save_err}"
                    logger.error(err_msg)
                    client.close()
                    return None, f"Error: Connection successful but {err_msg}", None

            return client, None, None
        except paramiko.BadHostKeyException as e:
            client.close()
            # Capture the new fingerprint even on mismatch for display
            new_fp = "SHA256:" + base64.b64encode(hashlib.sha256(e.key.asbytes()).digest()).decode('utf-8').replace('=', '')
            return None, "Host key mismatch", new_fp
        except paramiko.SSHException as e:
            client.close()
            msg = str(e)
            if "Host key verification failed" in msg:
                # Return the captured fingerprint if available
                return None, msg, capture_policy.fingerprint
            return None, msg, None
        except Exception as e:
            client.close()
            return None, str(e), None

    def test_server_connection(self, config: Dict, trust_host: bool = False) -> Tuple[bool, str, Optional[str]]:
        """Attempt to connect and return (success, message, fingerprint)."""
        client, err, fingerprint = self._get_ssh_client(config, trust_host=trust_host)
        if err:
            return False, err, fingerprint
        try:
            client.close()
            return True, "✅ Connection Successful!", None
        except Exception as e:
            return False, f"Error closing connection: {str(e)}", None

    def execute_command(self, alias: str, command: str) -> str:
        """Connect to a server by alias and execute a command with sudo and path resolution."""
        config = self.get_server_by_alias(alias)
        return self._execute_command_for_config(config, command, alias)

    def _execute_command_on_config(self, config: Dict, command: str) -> str:
        alias = config.get("alias", config.get("host", "unknown"))
        return self._execute_command_for_config(config, command, alias)

    def _execute_command_for_config(self, config: Dict, command: str, alias: str) -> str:
        client, err, _ = self._get_ssh_client(config)
        if err:
            logger.error(f"SSH Connection Error on '{alias}': {err}")
            return f"SSH Error: {err}"

        try:
            user = config.get('user', 'root')
            password = config.get('password')
            path_list = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

            if user != 'root' and command.startswith('sudo ') and password:
                cmd_body = command[5:]
                command_to_run = f'sudo -S -E env PATH="$PATH:{path_list}" {cmd_body}'
                stdin, stdout, stderr = client.exec_command(command_to_run, timeout=60)
                stdin.write(password + '\n')
                stdin.flush()
            else:
                command_to_run = f'env PATH="$PATH:{path_list}" {command}'
                stdin, stdout, stderr = client.exec_command(command_to_run, timeout=60)

            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')

            if "[sudo] password for" in error or "Password:" in error:
                error = SUDO_PROMPT_PATTERN.sub('', error).strip()

            if error and output:
                return f"{output}\n[Error Output]\n{error}"
            if error:
                return error
            return output
        except Exception as e:
            err_msg = f"SSH Execution Error on '{alias}': {str(e)}"
            logger.error(err_msg)
            return err_msg
        finally:
            client.close()

    def get_containers(self, alias: str) -> List[str]:
        """Fetch all container names from a server for autocomplete."""
        cmd = "sudo docker ps -a --format '{{.Names}}'"
        output = self.execute_command(alias, cmd)
        
        if "SSH Error" in output or "Error:" in output:
            return []
            
        containers = [name.strip() for name in output.split('\n') if name.strip()]
        return containers

    def execute_probe(self, config: Dict, command: str) -> str:
        return self._execute_command_on_config(config, command)

    def get_observability(self, alias: str, backup_path: str = "", include_docker: bool = False) -> Dict[str, str]:
        metrics = {
            "alias": alias,
            "cpu": "n/a",
            "ram": "n/a",
            "disk": "n/a",
            "uptime": "n/a",
            "docker_count": "n/a",
            "last_backup_age": "n/a",
            "status": "offline",
        }
        config = self.get_server_by_alias(alias)
        if not config:
            metrics["error"] = "Server not configured"
            return metrics

        command = (
            "printf 'cpu='; awk '/^cpu / {usage=($2+$4)*100/($2+$4+$5); printf \"%.1f%%\", usage}' /proc/stat; printf '\\n' && "
            "printf 'ram='; free -h | awk '/^Mem:/ {print $3 \"/\" $2}'; printf '\\n' && "
            "printf 'disk='; df -h / | awk 'NR==2 {print $3 \"/\" $2 \" (\" $5 \")\"}'; printf '\\n' && "
            "printf 'uptime='; uptime -p; printf '\\n'"
        )
        if include_docker:
            command += " && printf 'docker_count='; if command -v docker >/dev/null 2>&1; then sudo docker ps -q | wc -l | tr -d ' '; else printf 'n/a'; fi; printf '\\n'"
        if backup_path:
            safe_path = shlex.quote(backup_path)
            command += (
                f" && printf 'last_backup_age='; "
                f"if [ -f {safe_path} ]; then "
                f"  latest=$(stat -c %Y {safe_path}); "
                f"elif [ -d {safe_path} ]; then "
                f"  latest=$(find {safe_path} -type f -printf '%T@\\n' 2>/dev/null | sort -nr | head -n 1 | cut -d. -f1); "
                f"else latest=''; fi; "
                f"if [ -n \"$latest\" ]; then now=$(date +%s); printf '%ss' $((now-latest)); else printf 'n/a'; fi; printf '\\n'"
            )

        output = self.execute_command(alias, command)
        if "SSH Error" in output or "SSH Execution Error" in output:
            metrics["error"] = output
            return metrics

        metrics["status"] = "online"
        for line in output.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in metrics:
                metrics[key] = value.strip() or "n/a"
        metrics["last_backup_age"] = _humanize_age_seconds(metrics["last_backup_age"])
        return metrics

    def _build_capabilities_command(self, backup_path: str, include_docker: bool) -> str:
        # Batch checks into a single sudo command so execute_command can feed
        # the configured password for non-root SSH users.
        probe = "echo '---RESULTS---';"

        # Check sudo
        probe += " echo 'sudo_status=ok';"

        # Check docker
        if include_docker:
            probe += " if command -v docker >/dev/null 2>&1 && docker ps -q >/dev/null 2>&1; then echo 'docker_status=ok'; else echo 'docker_status=fail'; fi;"

        # Check backup path
        if backup_path:
            safe_path = shlex.quote(backup_path)
            probe += f" if test -e {safe_path}; then echo 'backup_status=ok'; else echo 'backup_status=missing'; fi;"

        return f"sudo sh -c {shlex.quote(probe)}"

    def _parse_capabilities_output(self, output: str, status: Dict[str, str]) -> None:
        lines = output.split('\n')
        for line in lines:
            if 'sudo_status=ok' in line:
                status["sudo"] = "ok"
            elif 'sudo_status=fail' in line:
                status["sudo"] = "fail"
                status["message"] = "Sudo failed or requires password"
            elif 'docker_status=ok' in line:
                status["docker"] = "ok"
            elif 'docker_status=fail' in line:
                status["docker"] = "fail"
            elif 'backup_status=ok' in line:
                status["backup"] = "ok"
            elif 'backup_status=missing' in line:
                status["backup"] = "missing"

    def check_server_capabilities(self, alias: str, backup_path: str = "", include_docker: bool = False) -> Dict[str, str]:
        status = {
            "alias": alias,
            "ssh": "fail",
            "sudo": "fail",
            "docker": "n/a",
            "known_host": "ok",
            "backup": "n/a",
            "message": "",
        }
        config = self.get_server_by_alias(alias)
        if not config:
            status["message"] = "Server not configured"
            return status

        ok, message, fingerprint = self.test_server_connection(config)
        if not ok:
            status["known_host"] = "missing" if fingerprint else "error"
            status["message"] = message
            return status
        status["ssh"] = "ok"

        command = self._build_capabilities_command(backup_path, include_docker)
        output = self.execute_command(alias, command)

        if "SSH Error" in output:
            status["message"] = output
            return status

        self._parse_capabilities_output(output, status)

        return status

    def container_action(self, alias: str, container_name: str, action: str) -> str:
        """Perform action (start, stop, restart) on a specific container."""
        safe_action = shlex.quote(action)
        safe_container = shlex.quote(container_name)
        cmd = f"sudo docker {safe_action} {safe_container}"
        return self.execute_command(alias, cmd)

    def get_container_logs(self, alias: str, container_name: str, lines: int = 50, search: Optional[str] = None) -> str:
        """Fetch recent logs for a container, optionally filtering by search term."""
        safe_lines = shlex.quote(str(lines))
        safe_container = shlex.quote(container_name)
        
        if search:
            safe_search = shlex.quote(search)
            cmd = f"sudo docker logs --tail {safe_lines} {safe_container} 2>&1 | grep -i -e {safe_search} | tail -n {safe_lines}"
        else:
            cmd = f"sudo docker logs --tail {safe_lines} {safe_container}"
            
        return self.execute_command(alias, cmd)

    def get_container_details(self, alias: str, container_name: str) -> str:
        """Fetch image, IP, and ports for a container using docker inspect."""
        format_str = (
            "Status: {{.State.Status}}\n"
            "Image: {{.Config.Image}}\n"
            "IP Address: {{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}\n"
            "Ports: {{range $p, $conf := .NetworkSettings.Ports}}{{$p}}{{if $conf}} -> {{(index $conf 0).HostPort}}{{end}} {{end}}"
        )
        safe_container = shlex.quote(container_name)
        cmd = f"sudo docker inspect --format '{format_str}' {safe_container}"
        return self.execute_command(alias, cmd)

    def get_system_stats(self, alias: str) -> str:
        """Fetch CPU, RAM, Disk, Load, and Uptime for the server."""
        cmd = (
            "echo \"[CPU Usage]\" && "
            "grep 'cpu ' /proc/stat | awk '{usage=($2+$4)*100/($2+$4+$5)} END {printf \"%.1f%%\\n\", usage}' && "
            "echo \"\" && echo \"[Memory Usage]\" && "
            "free -h | awk '/^Mem:/ {print $3 \"/\" $2}' && "
            "echo \"\" && echo \"[Disk Usage (root)]\" && "
            "df -h / | awk 'NR==2 {print $3 \"/\" $2 \" (\" $5 \")\"}' && "
            "echo \"\" && echo \"[Network Interfaces]\" && "
            "ip -4 -br addr show | awk '{print $1 \" -> \" $3}' && "
            "echo \"\" && echo \"[Total Traffic (RX/TX)]\" && "
            "cat /proc/net/dev | awk 'NR>2 {printf \"%s RX: %.2f GB, TX: %.2f GB\\n\", $1, $2/1024/1024/1024, $10/1024/1024/1024}' | sed 's/://' && "
            "echo \"\" && echo \"[Load Average]\" && "
            "cat /proc/loadavg | awk '{print $1 \", \" $2 \", \" $3}' && "
            "echo \"\" && echo \"[Uptime]\" && "
            "uptime -p"
        )
        return self.execute_command(alias, cmd)

    def get_log_files(self, alias: str) -> List[str]:
        """Fetch a list of common log files for autocomplete, with 5-min caching."""
        now = time.time()
        if alias in self._log_cache:
            ts, files = self._log_cache[alias]
            if now - ts < 300: # 5 minute cache
                return files

        cmd = (
            "sudo find /var/log /home -maxdepth 3 -type f "
            "\\( -name \"*.log\" -o -name \"syslog\" -o -name \"auth.log\" -o -name \"kern.log\" \\) "
            "2>/dev/null | head -n 50"
        )
        output = self.execute_command(alias, cmd)
        
        if "SSH Error" in output or "Error:" in output:
            return []
            
        files = [f.strip() for f in output.split('\n') if f.strip()]
        self._log_cache[alias] = (now, files)
        return files

    def resolve_remote_path(self, alias: str, remote_path: str) -> Optional[str]:
        """Resolve a remote path to its real path on the server to prevent symlink traversal."""
        # Use readlink -f or realpath (common on Ubuntu/Debian)
        cmd = f"realpath {shlex.quote(remote_path)}"
        output = self.execute_command(alias, cmd)
        
        if "SSH Error" in output or "Error:" in output:
            logger.warning("Failed to resolve remote path %r on %s: %s", remote_path, alias, output.strip())
            return None
            
        return output.strip()

    def server_power_action(self, alias: str, action: str) -> str:
        """Perform reboot or shutdown on a specific Ubuntu server."""
        if action not in ["reboot", "shutdown"]:
            return f"Error: Invalid action '{action}'. Use 'reboot' or 'shutdown'."
            
        cmd = "sudo reboot" if action == "reboot" else "sudo shutdown -h now"
        logger.info(f"Initiating {action} on '{alias}' via command: {cmd}")
        
        config = self.get_server_by_alias(alias)
        client, err, _ = self._get_ssh_client(config)
        if err:
            logger.error(f"SSH Connection Error for {action} on '{alias}': {err}")
            return f"SSH Error: {err}"

        try:
            transport = client.get_transport()
            channel = transport.open_session()
            channel.exec_command(cmd)
            return f"✅ Command `{cmd}` sent to `{alias}`. Server is {action}ing..."
        except Exception as e:
            if "EOFError" in str(type(e)) or "Connection reset" in str(e):
                return f"✅ Command `{cmd}` sent to `{alias}`. Server is {action}ing (Connection lost as expected)."
            
            err_msg = f"SSH Error during {action} on '{alias}': {str(e)}"
            logger.error(err_msg)
            return err_msg
        finally:
            client.close()
