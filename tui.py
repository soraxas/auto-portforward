#!/usr/bin/python
import asyncio
import curses
import time
import logging
import os
import sys

from typing import Dict, Set, List, Tuple
from remote_process_monitor import RemoteProcessMonitor
from datatype import Process
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
        self.needs_update = False

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
        return grouped

    def format_process_str(self, process: Process, is_selected: bool) -> str:
        pid_str = str(process.pid) if process.pid is not None else "N/A"
        # Get ports for this process
        ports = self.remote_monitor.connections.get(str(process.pid), [])
        ports_str = f" [Ports: {', '.join(map(str, ports))}]" if ports else ""

        return f"PID: {pid_str:<6} Name: {process.name:<20} Status: {process.status}{ports_str}"

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
            self.logger.error("Error in make_display: %s", e, exc_info=True)

    def move_cursor(self, direction: int) -> None:
        if not self.group_positions:
            return

        self.cursor_pos = (self.cursor_pos + direction) % len(self.group_positions)
        self.logger.debug("Moved cursor to position %d", self.cursor_pos)

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

    def handle_char(self, char: int) -> None:
        self.logger.debug("Handling character: %d", char)
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
        self.stdscr.nodelay(True)  # Set to non-blocking mode
        self.stdscr.timeout(10)    # 10ms timeout for getch
        self.stdscr.keypad(True)   # Enable keypad mode for arrow keys

        # Initial display
        self.make_display()

        while not self.done:
            try:
                # Handle input
                char = self.stdscr.getch()
                if char != curses.ERR:
                    self.logger.debug("Received key: %d", char)
                    if char == curses.KEY_RESIZE:
                        self.logger.debug("Window resize detected")
                        self.make_display()
                    else:
                        self.handle_char(char)
                        self.make_display()

                # Check for updates
                current_time = time.time()
                if current_time - self.last_update >= self.update_interval:
                    new_memory = self.remote_monitor.get_remote_processes()
                    if new_memory:  # Only update if we got valid data
                        self.last_memory = new_memory
                        self.make_display()
                    self.last_update = current_time

                await asyncio.sleep(0.001)  # 1ms sleep
            except Exception as e:
                self.logger.error("Error in run loop: %s", e, exc_info=True)

async def display_main(stdscr):
    # Get SSH host from command line arguments or use default
    import sys
    ssh_host = sys.argv[1] if len(sys.argv) > 1 else "soraxas@fait"
    logger.debug("Starting TUI with SSH host: %s", ssh_host)

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