#!/usr/bin/python
import asyncio
import curses
import time
from typing import Dict, Set, List, Tuple
from run import Process, update_process_listening_ports, MEMORY
import psutil

class ProcessDisplay:
    def __init__(self, stdscr: "_curses._CursesWindow"):
        self.stdscr = stdscr
        self.done = False
        self.selected_groups: Set[str] = set()  # Store selected groups instead of PIDs
        self.group_by = 'cwd'
        self.sort_reverse = False
        self.filter_text = ""
        self.last_memory: Dict[int, Process] = {}
        self.update_interval = 1.0  # seconds
        self.last_update = 0
        self.cursor_pos = 0  # Current cursor position
        self.group_positions: List[Tuple[int, str]] = []  # List of (y_pos, group) tuples
        self.refresh_count = 0

        # Initialize colors
        curses.start_color()
        curses.use_default_colors()
        # Define color pairs
        curses.init_pair(1, curses.COLOR_GREEN, -1)  # Header color
        curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Selected group color
        curses.init_pair(3, curses.COLOR_CYAN, -1)   # Cursor color
        curses.init_pair(4, curses.COLOR_MAGENTA, -1) # Ports color

    def get_grouped_processes(self) -> Dict[str, list[Process]]:
        grouped = {}
        for pid, process in MEMORY.items():
            if self.filter_text and self.filter_text.lower() not in process.name.lower():
                continue
            key = getattr(process, self.group_by)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(process)
        return grouped

    def format_process_str(self, process: Process, is_selected: bool, is_cursor: bool = False) -> str:
        pid_str = str(process.pid) if process.pid is not None else "N/A"
        # Get ports for this process
        ports = []
        for conn in psutil.net_connections():
            if conn.pid == process.pid and conn.status == 'LISTEN':
                ports.append(str(conn.laddr[1]))
        ports_str = f" [Ports: {', '.join(ports)}]" if ports else ""

        # Add cursor highlight
        cursor_prefix = ">" if is_cursor else " "
        return f"{cursor_prefix} PID: {pid_str:<6} Name: {process.name:<20} Status: {process.status}{ports_str}"

    def has_memory_changed(self) -> bool:
        if len(self.last_memory) != len(MEMORY):
            # fast return
            return True

        for pid, process in MEMORY.items():
            try:
                last_process = self.last_memory[pid]
            except KeyError:
                return True
            if (process != last_process):
                return True
        return False

    def make_display(self) -> None:
        # Clear the entire screen
        self.stdscr.erase()

        height, width = self.stdscr.getmaxyx()

        self.refresh_count += 1
        # Draw header
        header = f"Process Monitor - Group by: {self.group_by} [g] | Filter: {self.filter_text} | crn_refresh: {self.refresh_count}"
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

    def move_cursor(self, direction: int) -> None:
        if not self.group_positions:
            return

        self.cursor_pos = (self.cursor_pos + direction) % len(self.group_positions)
        self.make_display()

    def toggle_current_group(self) -> None:
        if not self.group_positions:
            return

        _, group = self.group_positions[self.cursor_pos]
        if group in self.selected_groups:
            self.selected_groups.remove(group)
        else:
            self.selected_groups.add(group)
        self.make_display()

    def handle_char(self, char: int) -> None:
        if char == ord('q'):
            self.done = True
        elif char == ord('g'):
            options = ['cwd', 'name', 'pid']
            current_index = options.index(self.group_by)
            self.group_by = options[(current_index + 1) % len(options)]
            self.cursor_pos = 0  # Reset cursor position when changing groups
            self.make_display()
        elif char == ord('s'):
            self.sort_reverse = not self.sort_reverse
            self.cursor_pos = 0  # Reset cursor position when sorting
            self.make_display()
        elif char == ord('f'):
            curses.echo()
            self.stdscr.addstr(curses.LINES-1, 0, "Enter filter text: ")
            self.filter_text = self.stdscr.getstr().decode('utf-8')
            curses.noecho()
            self.cursor_pos = 0  # Reset cursor position when filtering
            self.make_display()
        elif char == ord(' '):
            self.toggle_current_group()
        elif char == curses.KEY_UP:
            self.move_cursor(-1)
        elif char == curses.KEY_DOWN:
            self.move_cursor(1)

    async def run(self) -> None:
        curses.curs_set(0)  # Hide cursor
        self.stdscr.nodelay(True)
        self.stdscr.keypad(True)  # Enable keypad mode for arrow keys

        self.make_display()

        while not self.done:
            char = self.stdscr.getch()
            if char == curses.ERR:
                # Check for updates
                current_time = time.time()
                if current_time - self.last_update >= self.update_interval:
                    update_process_listening_ports()
                    if self.has_memory_changed():
                        self.last_memory = MEMORY.copy()
                        self.make_display()
                    self.last_update = current_time
                await asyncio.sleep(0.1)
            elif char == curses.KEY_RESIZE:
                self.make_display()
            else:
                self.handle_char(char)

async def display_main(stdscr):
    display = ProcessDisplay(stdscr)
    await display.run()

def main(stdscr) -> None:
    return asyncio.run(display_main(stdscr))

if __name__ == "__main__":
    curses.wrapper(main)