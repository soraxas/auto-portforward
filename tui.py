#!/usr/bin/python
import asyncio
import curses
import time
from typing import Dict, Set, List, Tuple
import subprocess
import json
import socket
import select
import logging
import os
from dataclasses import dataclass, asdict
import queue
import sys
import threading

# Configure logging
log_dir = os.path.expanduser("logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "tui.log")

# Create formatters
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Add file handler for main log
file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

logger = logging.getLogger("tui")

# Create a queue for logs
log_queue = queue.Queue()

# Custom handler to put logs in queue
class QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            log_queue.put(msg)
        except Exception:
            self.handleError(record)

# Configure remote logging
logger = logging.getLogger("remote_monitor")
logger.setLevel(logging.DEBUG)

# Add queue handler
queue_handler = QueueHandler()
queue_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(queue_handler)

@dataclass
class Process:
    pid: int
    name: str
    cwd: str
    status: str
    create_time: str

class RemoteProcessMonitor:
    def __init__(self, ssh_host: str):
        self.ssh_host = ssh_host
        self.connections = {}
        self.ssh_process = None
        self.socket = None
        self.conn = None
        self.logger = logging.getLogger("tui.remote_monitor")
        self.logger.debug("Initializing RemoteProcessMonitor for host: %s", ssh_host)

    def connect(self) -> bool:
        """Establish SSH connection and socket. Returns True if successful."""
        try:
            self.setup_connection()
            return True
        except Exception as e:
            self.logger.error("Failed to establish connection: %s", e, exc_info=True)
            return False

    def setup_connection(self):
        try:
            # Create a local socket for communication
            self.logger.debug("Creating local socket")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.bind(('localhost', 0))  # Bind to localhost
            self.socket.listen(1)
            port = self.socket.getsockname()[1]
            self.logger.debug("Created local socket on port %d", port)

            # Read the remote script
            script_path = os.path.join(os.path.dirname(__file__), 'remote_monitor.py')
            self.logger.debug("Reading remote script from: %s", script_path)
            with open(script_path, 'r') as f:
                remote_script = f.read()

            # Start the remote Python process that will connect back to us
            remote_cmd = f"python3 -c '{remote_script}' {port}"
            self.logger.debug("Starting SSH process with port forwarding")
            self.ssh_process = subprocess.Popen(
                ["ssh", "-R", f"{port}:localhost:{port}", self.ssh_host, remote_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                universal_newlines=True,
            )
            # Start threads to monitor stdout/stderr
            def log_output(pipe, prefix):
                for line in pipe:
                    self.logger.debug(f"SSH {prefix}: {line.strip()}")
                    # print(f"SSH {prefix}: {line.strip()}")
            threading.Thread(target=log_output, args=(self.ssh_process.stdout, "stdout"), daemon=True).start()
            threading.Thread(target=log_output, args=(self.ssh_process.stderr, "stderr"), daemon=True).start()

            # Accept the connection from the remote process
            self.logger.debug("Waiting for remote connection")
            self.conn, _ = self.socket.accept()
            self.logger.debug("Remote connection established")

        except Exception as e:
            self.logger.error(f"Error in setup_connection: {e}", exc_info=True)
            raise

    def get_remote_processes(self) -> Dict[int, Process]:
        try:
            # Check if there's data available
            ready = select.select([self.conn], [], [], 0.1)
            if ready[0]:
                # Read message length (4 bytes)
                length_bytes = self.conn.recv(4)
                if not length_bytes:
                    self.logger.debug("Connection closed by remote")
                    return self.last_memory if hasattr(self, 'last_memory') else {}
                if len(length_bytes) < 4:
                    self.logger.debug("Partial length bytes received: %s", length_bytes)
                    return self.last_memory if hasattr(self, 'last_memory') else {}

                length = int.from_bytes(length_bytes, 'big')
                self.logger.debug("Received message length: %d", length)

                # Read the full message
                data = b""
                remaining = length
                while remaining > 0:
                    chunk = self.conn.recv(min(remaining, 4096))
                    if not chunk:
                        self.logger.debug("Connection closed while reading message")
                        break
                    data += chunk
                    remaining -= len(chunk)
                    self.logger.debug("Read %d bytes, %d remaining", len(chunk), remaining)

                if len(data) == length:
                    try:
                        info = json.loads(data.decode())
                        if info.get("type") == "log":
                            # Handle log message
                            self.logger.info("Remote: %s", info['message'])
                        elif info.get("type") == "data":
                            # Handle process data
                            self.connections = info["connections"]
                            processes = {int(pid): Process(**proc) for pid, proc in info["processes"].items()}
                            # self.logger.debug("Received update with %d processes and %d connections", len(processes), len(self.connections))
                            return processes
                    except json.JSONDecodeError as e:
                        self.logger.error("Error decoding JSON: %s", e)
                        self.logger.debug("Problematic data: %s", data)
                else:
                    self.logger.debug("Incomplete message: got %d bytes, expected %d", len(data), length)

        except Exception as e:
            self.logger.error("Error reading from socket: %s", e, exc_info=True)

        return self.last_memory if hasattr(self, 'last_memory') else {}

    def cleanup(self):
        self.logger.debug("Cleaning up remote monitor")
        if self.ssh_process:
            self.logger.debug("Terminating SSH process")
            self.ssh_process.terminate()
        if self.conn:
            self.logger.debug("Closing connection")
            self.conn.close()
        if self.socket:
            self.logger.debug("Closing socket")
            self.socket.close()

class ProcessDisplay:
    def __init__(self, stdscr: "_curses._CursesWindow", remote_monitor: RemoteProcessMonitor):
        self.logger = logging.getLogger("tui.display")
        self.logger.debug("Initializing ProcessDisplay")
        self.stdscr = stdscr
        self.done = False
        self.selected_groups: Set[str] = set()
        self.group_by = 'cwd'
        self.sort_reverse = False
        self.filter_text = ""
        self.last_memory: Dict[int, Process] = {}
        self.update_interval = 1.0
        self.last_update = 0
        self.cursor_pos = 0
        self.group_positions: List[Tuple[int, str]] = []
        self.refresh_count = 0
        self.remote_monitor = remote_monitor

        # Initialize colors
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)  # Header color
        curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Selected group color
        curses.init_pair(3, curses.COLOR_CYAN, -1)   # Cursor color
        curses.init_pair(4, curses.COLOR_MAGENTA, -1) # Ports color
        self.logger.debug("Display initialization complete")

    def cleanup(self):
        self.logger.debug("Cleaning up display")
        self.remote_monitor.cleanup()

    def get_grouped_processes(self) -> Dict[str, list[Process]]:
        grouped = {}
        for pid, process in self.last_memory.items():
            if self.filter_text and self.filter_text.lower() not in process.name.lower():
                continue
            key = getattr(process, self.group_by)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(process)
        # self.logger.debug(f"Grouped {len(self.last_memory)} processes into {len(grouped)} groups")
        return grouped

    def format_process_str(self, process: Process, is_selected: bool) -> str:
        pid_str = str(process.pid) if process.pid is not None else "N/A"
        # Get ports for this process
        ports = self.remote_monitor.connections.get(str(process.pid), [])
        ports_str = f" [Ports: {', '.join(map(str, ports))}]" if ports else ""

        return f"PID: {pid_str:<6} Name: {process.name:<20} Status: {process.status}{ports_str}"

    def has_memory_changed(self) -> bool:
        if len(self.last_memory) != len(self.remote_monitor.get_remote_processes()):
            self.logger.debug("Memory size changed")
            return True

        new_memory = self.remote_monitor.get_remote_processes()
        for pid, process in new_memory.items():
            if pid not in self.last_memory:
                self.logger.debug(f"New process found: {pid}")
                return True
            if process != self.last_memory[pid]:
                self.logger.debug(f"Process changed: {pid}")
                return True
        return False

    def make_display(self) -> None:
        try:
            # Clear the entire screen
            self.stdscr.clear()

            height, width = self.stdscr.getmaxyx()

            self.refresh_count += 1
            # Draw header
            header = f"Process Monitor - Group by: {self.group_by} [g] | Filter: {self.filter_text} | Refresh: {self.refresh_count}"
            self.stdscr.addstr(0, 0, header[:width-1], curses.color_pair(1))

            # Draw processes
            y = 2
            self.group_positions = []  # Reset group positions
            grouped = self.get_grouped_processes()
            self.logger.debug("Grouped processes: %s", grouped)
            sorted_groups = sorted(
                grouped.items(),
                key=lambda x: str(x[0]) if x[0] is not None else "",
                reverse=self.sort_reverse
            )

            for group, processes in sorted_groups:
                if y >= height - 2:
                    break

                group_key = str(group) if group is not None else "Unknown"
                is_group_selected = group_key in self.selected_groups
                is_cursor = len(self.group_positions) == self.cursor_pos

                # Draw group header with color
                group_header = f"[{group_key}]"
                color = curses.color_pair(2) if is_group_selected else curses.color_pair(1)
                if is_cursor:
                    group_header = ">" + group_header
                self.stdscr.addstr(y, 0, group_header[:width-1], color)
                self.group_positions.append((y, group_key))
                y += 1

                for process in sorted(processes, key=lambda p: str(p.pid) if p.pid is not None else "0", reverse=self.sort_reverse):
                    if y >= height - 2:
                        break

                    process_str = self.format_process_str(process, is_group_selected)

                    # Apply colors
                    if is_group_selected:
                        self.stdscr.addstr(y, 2, process_str[:width-3], curses.color_pair(2))
                    else:
                        # Split the string to color the ports part
                        main_part = process_str.split("[Ports:")[0]
                        ports_part = "[Ports:" + process_str.split("[Ports:")[1] if "[Ports:" in process_str else ""
                        self.stdscr.addstr(y, 2, main_part[:width-3])
                        if ports_part:
                            self.stdscr.addstr(ports_part[:width-3], curses.color_pair(4))
                    y += 1
                y += 1

            # Clear remaining lines
            for i in range(y, height-1):
                self.stdscr.move(i, 0)
                self.stdscr.clrtoeol()

            # Draw footer
            footer = "Controls: [↑/↓]move between groups [g]roup by [s]ort [f]ilter [SPACE]toggle group [q]uit"
            self.stdscr.addstr(height-1, 0, footer[:width-1], curses.color_pair(1))

            # Ensure the screen is updated
            self.stdscr.refresh()
        except Exception as e:
            self.logger.error(f"Error in make_display: {e}", exc_info=True)

    def move_cursor(self, direction: int) -> None:
        if not self.group_positions:
            return

        self.cursor_pos = (self.cursor_pos + direction) % len(self.group_positions)
        self.logger.debug("Moved cursor to position %d", self.cursor_pos)
        self.make_display()

    def toggle_current_group(self) -> None:
        if not self.group_positions:
            return

        _, group = self.group_positions[self.cursor_pos]
        if group in self.selected_groups:
            self.selected_groups.remove(group)
            self.logger.debug("Deselected group: %s", group)
        else:
            self.selected_groups.add(group)
            self.logger.debug("Selected group: %s", group)
        self.make_display()

    def handle_char(self, char: int) -> None:
        try:
            if char == ord('q'):
                self.logger.debug("Quit command received")
                self.done = True
            elif char == ord('g'):
                options = ['cwd', 'name', 'pid']
                current_index = options.index(self.group_by)
                self.group_by = options[(current_index + 1) % len(options)]
                self.cursor_pos = 0  # Reset cursor position when changing groups
                self.logger.debug("Changed group by to: %s", self.group_by)
            elif char == ord('s'):
                self.sort_reverse = not self.sort_reverse
                self.cursor_pos = 0  # Reset cursor position when sorting
                self.logger.debug("Changed sort direction to: %s", 'reverse' if self.sort_reverse else 'forward')
            elif char == ord('f'):
                curses.echo()
                self.stdscr.addstr(curses.LINES-1, 0, "Enter filter text: ")
                self.filter_text = self.stdscr.getstr().decode('utf-8')
                curses.noecho()
                self.cursor_pos = 0  # Reset cursor position when filtering
                self.logger.debug("Set filter text to: %s", self.filter_text)
            elif char == ord(' '):
                self.toggle_current_group()
            elif char == curses.KEY_UP:
                self.move_cursor(-1)
            elif char == curses.KEY_DOWN:
                self.move_cursor(1)
            else:
                self.logger.debug("Unhandled key: %d", char)
        except Exception as e:
            self.logger.error("Error handling character: %s", e, exc_info=True)

    async def run(self) -> None:
        self.logger.debug("Starting display run loop")
        curses.curs_set(0)  # Hide cursor
        self.stdscr.nodelay(True)
        self.stdscr.keypad(True)  # Enable keypad mode for arrow keys

        # Initial display
        self.make_display()

        while not self.done:
            try:
                char = self.stdscr.getch()
                if char != curses.ERR:
                    self.logger.debug("Received key: %d", char)
                    if char == curses.KEY_RESIZE:
                        self.logger.debug("Window resize detected")
                        self.make_display()
                    else:
                        self.handle_char(char)
                        self.make_display()  # Update display after handling key
                else:
                    # Check for updates
                    current_time = time.time()
                    if current_time - self.last_update >= self.update_interval:
                        new_memory = self.remote_monitor.get_remote_processes()
                        if new_memory:  # Only update if we got valid data
                            self.last_memory = new_memory
                            self.make_display()
                        self.last_update = current_time
                    await asyncio.sleep(0.1)
            except Exception as e:
                self.logger.error("Error in run loop: %s", e, exc_info=True)

async def display_main(stdscr):
    # Get SSH host from command line arguments or use default
    import sys
    ssh_host = sys.argv[1] if len(sys.argv) > 1 else "soraxas@fait"
    logger.debug(f"Starting TUI with SSH host: {ssh_host}")

    # First establish the connection
    remote_monitor = RemoteProcessMonitor(ssh_host)
    if not remote_monitor.connect():
        logger.error("Failed to establish connection. Exiting.")
        return

    # Now create the display
    display = ProcessDisplay(stdscr, remote_monitor)
    try:
        await display.run()
    finally:
        display.cleanup()

def main(stdscr) -> None:
    return asyncio.run(display_main(stdscr))

if __name__ == "__main__":

    # Try to establish connection before entering curses
    ssh_host = sys.argv[1] if len(sys.argv) > 1 else "soraxas@fait"
    remote_monitor = RemoteProcessMonitor(ssh_host)
    if not remote_monitor.connect():
        logger.error("Failed to establish connection. Exiting.")
        sys.exit(1)

    # Now enter curses
    curses.wrapper(main)