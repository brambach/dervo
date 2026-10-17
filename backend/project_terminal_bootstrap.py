"""Fresh-process PTY setup. Never imported by the threaded host."""
import os
import sys


def main():
    slave, shell = int(sys.argv[1]), sys.argv[2]
    # login_tty creates a session, acquires the controlling terminal and replaces
    # standard streams. Running it after exec avoids Python after a threaded fork.
    os.login_tty(slave)
    os.execve(shell, [shell, '-l', '-i'], dict(os.environ))


if __name__ == '__main__':
    main()
