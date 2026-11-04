> **Note:** This program was written entirely by Perplexity AI. I only accompanied the development: I came up with the ideas and made the design decisions. Every line of code, however, comes from Perplexity AI. Credit where credit is due. This is meant to be a community project. I suspect I won't have much, or any, time to add new features or fix bugs. Feel free to add your own changes, fixes, or new gadgets.

My development goal: a simple, reliable tool that does one job. I have been running it daily through the Windows Task Scheduler for months, and it works so well that other people might find it useful too.

I hope you like it,

Dietmar Bos

# SyncDisk

A small Python tool that mirrors one directory (or a whole drive) to another. The first run copies everything. Every later run only transfers what has changed, so it finishes quickly.

> [!WARNING]
> **SyncDisk is a one-way mirror, not a two-way sync.** The target is made identical to the source. Files in the target that do not exist in the source are **deleted**. Never point it at a target that contains data you want to keep. Nothing outside the target directory you specify is ever touched. If in doubt, run with `--dry-run` first.

## Why This Exists

I wanted a dependable daily backup of my data disk to a second disk, without a heavy backup suite. SyncDisk is a single Python file with no dependencies, easy to read, easy to schedule, and easy to adapt.

## Features

- **One-way mirroring:** source to target, the target becomes an exact copy
- **Fast incremental runs:** only the first run takes long; later runs compare file size and modification time and copy only the differences
- **Whole-drive backups:** `python syncDisk3.py C:\ D:\` works
- **Preserves timestamps:** files are copied with `shutil.copy2`
- **Safe copying:** each file is copied to a temporary name first and then moved into place, so an aborted copy never leaves a half-written file
- **Tolerant of problems:** locked, unreadable or read-only items are logged and skipped; the run continues
- **Safety checks:** refuses to run when source and target are identical or nested, and refuses to mirror an empty source onto a non-empty target
- **Dry run:** `--dry-run` shows what would happen without changing anything
- **Waits for sleeping or late-mounted drives:** up to 60 seconds per drive
- **Logging:** a single rolling log file (max. 5 MB, plus one backup), plus console output
- **Scheduler friendly:** meaningful exit codes

## Installation

### Requirements

- Python 3.8 or higher
- Windows (built and tested for Windows and the Task Scheduler)
- No third-party packages

### Running the Program

```
python syncDisk3.py <source> <target> [options]
```

Examples:

```
python syncDisk3.py "D:\Data" "E:\Backup\Data"
python syncDisk3.py C:\ D:\
python syncDisk3.py "D:\Data" "E:\Backup\Data" --dry-run
```

Paths containing spaces must be quoted. A trailing backslash before a closing quote (`"D:\Data\"`) is tolerated, but writing `"D:\Data"` is cleaner.

### Options

| Option | Meaning |
|---|---|
| `--dry-run` | Only log what would be done, change nothing |
| `--allow-empty` | Allow mirroring an empty source (this wipes the target) |
| `--log-dir DIR` | Write the log file to DIR instead of the program folder |

## How It Works

1. Both drives are woken up and checked for availability.
2. Safety checks: the source must exist, source and target must be different and not nested.
3. Source and target are scanned recursively. Symlinks and junctions are not followed.
4. Missing directories are created, missing files are copied, and files whose size or modification time differ are overwritten.
5. Everything in the target that is not in the source is removed (files first, then empty directories). Places that could not be read in the source are protected from deletion in the target.
6. A summary is logged: copied, updated, deleted, directories created, renamed, skipped, errors.

### Running Daily with the Task Scheduler

Create a basic task with a daily trigger and this action:

- Program: `python.exe` (full path)
- Arguments: `C:\Tools\syncDisk3.py "D:\Data" "E:\Backup\Data"`
- Start in: the folder of the script

## Backing Up a Whole Drive

`python syncDisk3.py C:\ D:\` mirrors drive C: to drive D:. Notes:

- Windows system items at the drive root (`System Volume Information`, `$Recycle.Bin`, `pagefile.sys`, `hiberfil.sys`, `swapfile.sys`, `DumpStack.log.tmp`) are skipped and never deleted in the target.
- Files in use by other programs may be locked. They are logged and skipped, and the run ends with exit code 7.
- This is a file-level copy, **not** a bootable system image.
- Run it with `--dry-run` first and read the log.

## Logging

All runs write to one log file, `syncDisk.log`, in the folder of the program (or the folder given with `--log-dir`). Each run starts with a separator line, so runs are easy to tell apart.

When the file reaches 5 MB, it is renamed to `syncDisk.log.1` and a new `syncDisk.log` is started. An older `syncDisk.log.1` is overwritten. The logs therefore never take up more than about 10 MB. The log files themselves are never mirrored.

## Technical Details

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | Finished without errors |
| 1 | Invalid arguments |
| 2 | Source does not exist or is not a directory |
| 3 | Source drive not reachable |
| 4 | Target drive not reachable (or target is not a directory) |
| 5 | Source is empty but target is not (use `--allow-empty` to override) |
| 6 | Source and target are identical or nested |
| 7 | Finished, but some items could not be processed (see log) |

### Design Principles

- **Comparison by size and modification time**, no checksums. That keeps repeat runs fast. A tolerance of 2 seconds accommodates FAT/exFAT targets. A file edited within 2 seconds that keeps exactly the same size would not be noticed.
- **Case-insensitive path matching on Windows.** A rename that only changes upper/lower case is mirrored.
- **Symlinks and junctions are skipped**, never followed, and whatever exists under that name in the target is left alone.
- **Deletion is conservative:** a directory in the target is only removed when it is empty.
- Very long paths (over 260 characters) may fail on systems without long path support; such items are logged and skipped.

## Frequently Asked Questions

**Why is the first run so slow?**
Everything has to be copied. Afterwards only changes are transferred, so later runs are quick.

**Files disappeared from my target folder!**
That is how a mirror works. Anything in the target that is not in the source is removed.

**Is it a two-way sync?**
No. Changes made in the target are overwritten or deleted on the next run.

**Does it touch anything outside the target?**
No.

**The run ended with exit code 7.**
Some files could not be processed, usually because they were locked. Check the log for lines starting with `SKIP`.

**Every run copies the same files again.**
Your target file system probably stores timestamps differently. NTFS works fine. FAT/exFAT are handled by the 2-second tolerance.

**It says the source is empty and refuses to run.**
This protects you from wiping the target when a drive is not mounted correctly. Use `--allow-empty` only if you really want that.

**Where are the log files?**
In the folder of the script (or the EXE, if you build one with PyInstaller), as `syncDisk.log` and `syncDisk.log.1`. Use `--log-dir` to change the location.

## Credits

Developed entirely by Perplexity AI. Ideas, testing and design decisions by Dietmar Bos.

## License

MIT License. See the [LICENSE](LICENSE) file.

## Support

This is a community project. If you have questions or run into problems:

- Open an issue on GitHub
- Describe your problem in as much detail as possible, ideally including the log file
- I'll try to help, but can't guarantee a response

**Note:** This project was developed entirely by AI (Perplexity AI).

## Changelog

### Version 1.0 (November 2026)

- Initial release
- One-way mirroring with fast incremental runs
- Safe copying, error-tolerant processing, safety checks, dry run
- Drive wake-up and wait logic
- Rolling log file (5 MB limit)
