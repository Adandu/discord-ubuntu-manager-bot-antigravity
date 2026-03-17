import os
import json
import paramiko
import io
import logging
from typing import List, Dict, Optional

logger = logging.getLogger('discobunty.ssh')

class SSHManager:
    def __init__(self):
        self.servers = self._load_servers()

    def _load_servers(self) -> List[Dict]:
        """Load servers from individual environment variables or SERVERS_JSON."""
        servers = []
        
        # 1. Try loading from numbered environment variables (v0.2.0 style)
        i = 1
        while True:
            alias = os.getenv(f'DISCORD_UBUNTU_SERVER_ALIAS_{i}')
            if not alias:
                break
            
            server = {
                "alias": alias,
                "host": os.getenv(f'DISCORD_UBUNTU_SERVER_IP_{i}'),
                "user": os.getenv(f'DISCORD_UBUNTU_SERVER_USER_{i}', 'root'),
                "port": int(os.getenv(f'DISCORD_UBUNTU_SERVER_PORT_{i}', '22')),
                "auth_method": os.getenv(f'DISCORD_UBUNTU_SERVER_AUTH_METHOD_{i}', 'key').lower(),
                "password": os.getenv(f'DISCORD_UBUNTU_SERVER_PASSWORD_{i}'),
                "key": os.getenv(f'DISCORD_UBUNTU_SERVER_KEY_{i}')
            }
            servers.append(server)
            i += 1

        # 2. Backward compatibility for SERVERS_JSON (v0.1.0 style)
        if not servers:
            servers_raw = os.getenv('SERVERS_JSON')
            if servers_raw:
                try:
                    servers = json.loads(servers_raw)
                    logger.info("Loaded servers from legacy SERVERS_JSON.")
                except json.JSONDecodeError:
                    logger.error("Invalid JSON in SERVERS_JSON environment variable.")
        
        if servers:
            logger.info(f"Initialized SSHManager with {len(servers)} servers.")
        else:
            logger.warning("No servers configured! Check your environment variables.")
            
        return servers

    def get_server_aliases(self) -> List[str]:
        """Return a list of all server aliases for autocomplete."""
        return [s['alias'] for s in self.servers]

    def get_server_by_alias(self, alias: str) -> Optional[Dict]:
        """Find a server configuration by its alias."""
        for s in self.servers:
            if s['alias'] == alias:
                return s
        return None

    def execute_command(self, alias: str, command: str) -> str:
        """Connect to a server by alias and execute a command."""
        logger.info(f"Executing command on '{alias}': {command}")
        config = self.get_server_by_alias(alias)
        if not config:
            err_msg = f"Error: Server alias '{alias}' not found."
            logger.error(err_msg)
            return err_msg

        # Extract connection details
        host = config.get('host')
        user = config.get('user', 'root')
        port = config.get('port', 22)
        auth_method = config.get('auth_method', 'key')

        # Setup SSH Client
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            if auth_method == 'key':
                # Get key value
                key_value = config.get('key') or os.getenv(config.get('secret_env', ''))
                
                if not key_value:
                    return f"Error: SSH Key not provided for '{alias}'."

                # If the value looks like a path (starts with /), treat it as a file
                if key_value.startswith('/') or (os.path.exists(key_value) and os.path.isfile(key_value)):
                    if not os.path.exists(key_value):
                        return f"Error: SSH Key file not found at '{key_value}' for '{alias}'."
                    client.connect(hostname=host, port=port, username=user, key_filename=key_value, timeout=10)
                else:
                    # Treat as raw key string - try multiple formats
                    private_key = None
                    key_errors = []
                    for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey]:
                        try:
                            private_key = key_class.from_private_key(io.StringIO(key_value))
                            if private_key: break
                        except Exception as e:
                            key_errors.append(f"{key_class.__name__}: {str(e)}")
                    
                    if not private_key:
                        return f"Error: Could not parse SSH key string for '{alias}'. Errors: {'; '.join(key_errors)}"
                    
                    client.connect(hostname=host, port=port, username=user, pkey=private_key, timeout=10)
            else:
                # Handle Password
                password = config.get('password') or os.getenv(config.get('secret_env', ''))
                if not password:
                    return f"Error: Password not provided for '{alias}'."
                client.connect(hostname=host, port=port, username=user, password=password, timeout=10)

            # Execute Command
            stdin, stdout, stderr = client.exec_command(command)
            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')

            client.close()
            
            if error and not output:
                logger.warning(f"Command on '{alias}' produced error output: {error.strip()}")
                return error
            
            return output

        except Exception as e:
            err_msg = f"SSH Error on '{alias}': {str(e)}"
            logger.error(err_msg)
            return err_msg

    def get_containers(self, alias: str) -> List[str]:
        """Fetch all container names from a server for autocomplete."""
        # Use --all to include stopped containers
        cmd = "sudo docker ps -a --format '{{.Names}}'"
        output = self.execute_command(alias, cmd)
        
        if "SSH Error" in output or "Error:" in output:
            return []
            
        containers = [name.strip() for name in output.split('\n') if name.strip()]
        return containers

    def container_action(self, alias: str, container_name: str, action: str) -> str:
        """Perform action (start, stop, restart) on a specific container."""
        cmd = f"sudo docker {action} {container_name}"
        return self.execute_command(alias, cmd)

    def get_container_logs(self, alias: str, container_name: str, lines: int = 50) -> str:
        """Fetch recent logs for a container."""
        cmd = f"sudo docker logs --tail {lines} {container_name}"
        return self.execute_command(alias, cmd)

    def get_container_details(self, alias: str, container_name: str) -> str:
        """Fetch image, IP, and ports for a container using docker inspect."""
        # Format the inspect output to get specific fields
        # Using literal newlines in the Python string ensures they are sent correctly via SSH
        format_str = (
            "Status: {{.State.Status}}\n"
            "Image: {{.Config.Image}}\n"
            "IP Address: {{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}\n"
            "Ports: {{range $p, $conf := .NetworkSettings.Ports}}{{$p}}{{if $conf}} -> {{(index $conf 0).HostPort}}{{end}} {{end}}"
        )
        cmd = f"sudo docker inspect --format '{format_str}' {container_name}"
        return self.execute_command(alias, cmd)

    def get_system_stats(self, alias: str) -> str:
        """Fetch CPU, RAM, Disk, Load, and Uptime for the server."""
        # Combine several commands to get a full snapshot
        # CPU usage (100 - idle), Memory (Used/Total), Disk (Used/Total on /), Load Avg, Uptime
        # Plus Network Interfaces and Total Traffic
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
