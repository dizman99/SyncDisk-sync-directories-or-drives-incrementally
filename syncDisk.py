"""
syncDisk3.py - one-way directory mirror (source -> target).

The target becomes an exact copy of the source: files that exist only in the
target are DELETED. Nothing outside the target directory is ever touched.
The first run copies everything; later runs only transfer differences.

Usage:   python syncDisk3.py <source> <target> [--dry-run] [--allow-empty]
                             [--log-dir DIR]

Logging: one rolling log file "syncDisk.log" (max. 5 MB) plus one backup
         "syncDisk.log.1". When the limit is reached, the oldest content is dropped.

Exit codes:
  0  finished without errors
  1  invalid arguments
  2  source does not exist / is not a directory
  3  source drive not reachable
  4  target drive not reachable
  5  source is empty but target is not (refused, use --allow-empty)
  6  source and target are identical or nested
  7  finished, but some items could not be processed (see log)
"""
import os
import sys
import time
import stat
import shutil
import logging
import argparse
from logging.handlers import RotatingFileHandler
from pathlib import Path

CASE_INSENSITIVE = (os.name == "nt")
MTIME_TOLERANCE = 2.0            # seconds; FAT/exFAT store timestamps with 2 s resolution
TMP_SUFFIX = ".syncdisk.tmp"
REPARSE_POINT = 0x400            # FILE_ATTRIBUTE_REPARSE_POINT (junctions etc.)

LOG_NAME = "syncDisk.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # size limit of the active log file
LOG_BACKUPS = 1                  # one older file (syncDisk.log.1), so history is not lost at rollover

# Windows system items at the drive root that cannot / should not be mirrored
ROOT_EXCLUDES = {
    "system volume information", "$recycle.bin", "pagefile.sys",
    "hiberfil.sys", "swapfile.sys", "dumpstack.log.tmp",
}

# ---------------------------------------------------------------------------
# Logging setup: rolling log file (size-limited) plus console
# ---------------------------------------------------------------------------
def getAppDir() -> Path:
    # Correct path detection, whether running as a script or a compiled EXE
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent      # PyInstaller EXE
    return Path(__file__).resolve().parent      # plain .py script

def setupLogging(logDir: Path):
    fmt = "%(asctime)s %(levelname)-8s %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    try:
        logDir.mkdir(parents=True, exist_ok=True)
        logFile = logDir / LOG_NAME
        logging.basicConfig(
            level=logging.DEBUG, format=fmt, datefmt=datefmt,
            handlers=[RotatingFileHandler(logFile, maxBytes=LOG_MAX_BYTES,
                                          backupCount=LOG_BACKUPS, encoding="utf-8"),
                      logging.StreamHandler(sys.stdout)],
        )
        logging.info(f"Log file: {logFile} (max. {LOG_MAX_BYTES // (1024 * 1024)} MB, rolling)")
        return logFile
    except Exception as e:
        # Fallback: console only, so the run is still visible
        logging.basicConfig(level=logging.DEBUG, format=fmt, datefmt=datefmt,
                            handlers=[logging.StreamHandler(sys.stdout)], force=True)
        logging.error(f"Could not create log file, logging to console only: {e}")
        return None

# ---------------------------------------------------------------------------
# Wake up the disk: repeated access attempts on the drive root
# ---------------------------------------------------------------------------
def wakeDisk(path: Path, attempts: int = 5, waitSeconds: int = 8):
    drive = Path(path).anchor
    logging.info(f"Waking drive {drive} ...")
    for i in range(attempts):
        try:
            _ = list(Path(drive).iterdir())
            logging.info(f"Drive {drive} responded (attempt {i+1})")
            return True
        except Exception as e:
            logging.warning(f"Drive {drive} not ready yet (attempt {i+1}): {e}")
            if i < attempts - 1:
                time.sleep(waitSeconds)
    return False

# ---------------------------------------------------------------------------
# Wait until the drive is accessible
# ---------------------------------------------------------------------------
def waitUntilAccessible(path: Path, timeout: int = 60) -> bool:
    drive = Path(path).anchor
    logging.info(f"Checking accessibility of {drive} (max. {timeout}s) ...")
    start = time.time()
    while time.time() - start < timeout:
        if Path(drive).exists():
            logging.info(f"Drive {drive} is accessible.")
            return True
        logging.debug(f" ... waiting for {drive}")
        time.sleep(2)
    logging.error(f"Drive {drive} not accessible after {timeout}s!")
    return False

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def keyOf(rel: str) -> str:
    """Comparison key for a relative path (case-insensitive on Windows)."""
    return rel.lower() if CASE_INSENSITIVE else rel

def makeWritable(path):
    """Clear the read-only flag if necessary (never alters other permissions)."""
    try:
        if not os.access(path, os.W_OK):
            os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    except Exception:
        pass

def _rmError(func, path, *_):
    makeWritable(path)
    func(path)

def removeTree(path: Path):
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_rmError)
    else:
        shutil.rmtree(path, onerror=_rmError)

def removeFileOrLink(path: Path, retries: int = 3):
    """Delete a file or a symlink/junction (the link itself, never its content)."""
    last = None
    for _ in range(retries):
        try:
            makeWritable(path)
            try:
                os.unlink(path)
            except (PermissionError, IsADirectoryError):
                if path.is_symlink() or not path.is_dir():
                    raise
                os.rmdir(path)          # directory junction / symlink
            return
        except FileNotFoundError:
            return
        except OSError as e:
            last = e
            time.sleep(0.2)
    raise last if last is not None else RuntimeError("no attempt was made")

def copyFile(srcPath: Path, dstPath: Path, retries: int = 2):
    """Copy via a temporary file, so an aborted copy never leaves a half file."""
    tmp = dstPath.with_name(dstPath.name + TMP_SUFFIX)
    last = None
    for _ in range(retries):
        try:
            shutil.copy2(srcPath, tmp)
            if dstPath.exists():
                makeWritable(dstPath)
            os.replace(tmp, dstPath)
            return
        except OSError as e:
            last = e
            try:
                if tmp.exists():
                    makeWritable(tmp)
                    tmp.unlink()
            except OSError:
                pass
            time.sleep(0.5)
    raise last if last is not None else RuntimeError("no attempt was made")

def ensureParent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

def isInside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:                  # different drives
        return False

def isProtected(key: str, protectedKeys: set) -> bool:
    """True if key itself or any of its parent directories is protected."""
    if not protectedKeys:
        return False
    parts = key.split("/")
    for i in range(1, len(parts) + 1):
        if "/".join(parts[:i]) in protectedKeys:
            return True
    return False

# ---------------------------------------------------------------------------
# Scanning (does not follow symlinks / junctions)
# ---------------------------------------------------------------------------
class ScanResult:
    def __init__(self):
        self.entries = {}       # key -> (kind, size, mtime, relActual); kind: dir/file/link
        self.unreadable = set() # keys whose content could not be read
        self.errors = 0
        self.rootFailed = False

def scanTree(root: Path, skipPaths=()) -> ScanResult:
    res = ScanResult()
    skip = {os.path.normcase(os.path.abspath(p)) for p in skipPaths}
    stack = [""]
    while stack:
        relDir = stack.pop()
        full = root / relDir if relDir else root
        try:
            with os.scandir(full) as it:
                items = list(it)
        except OSError as e:
            res.errors += 1
            logging.warning(f"Cannot read directory {full}: {e}")
            if relDir:
                res.unreadable.add(keyOf(relDir))
            else:
                res.rootFailed = True
            continue
        for e in items:
            rel = f"{relDir}/{e.name}" if relDir else e.name
            key = keyOf(rel)
            if not relDir and CASE_INSENSITIVE and e.name.lower() in ROOT_EXCLUDES:
                logging.debug(f"Excluded system item: {e.path}")
                continue
            if skip and os.path.normcase(os.path.abspath(e.path)) in skip:
                continue
            try:
                if e.is_symlink():
                    res.entries[key] = ("link", 0, 0.0, rel)
                    continue
                st = e.stat(follow_symlinks=False)
                if e.is_dir(follow_symlinks=False):
                    if getattr(st, "st_file_attributes", 0) & REPARSE_POINT:
                        res.entries[key] = ("link", 0, 0.0, rel)    # junction
                    else:
                        res.entries[key] = ("dir", 0, 0.0, rel)
                        stack.append(rel)
                elif e.is_file(follow_symlinks=False):
                    res.entries[key] = ("file", st.st_size, st.st_mtime, rel)
            except FileNotFoundError:
                pass                    # vanished while scanning
            except OSError as ex:
                res.errors += 1
                res.unreadable.add(key)
                logging.warning(f"Cannot access {e.path}: {ex}")
    return res

# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------
def sync(src: Path, dst: Path, dryRun: bool = False, allowEmpty: bool = False,
         skipPaths=()):
    """Returns (exitCode, stats dict)."""
    logging.info("Scanning source ...")
    srcScan = scanTree(src, skipPaths)
    logging.info(f" {len(srcScan.entries)} entries found in source")
    if srcScan.rootFailed:
        logging.critical("Source root directory could not be read - aborting, nothing was changed.")
        return 2, {}

    logging.info("Scanning target ...")
    dstScan = scanTree(dst, skipPaths)
    logging.info(f" {len(dstScan.entries)} entries found in target")
    if dstScan.rootFailed:
        logging.critical("Target root directory could not be read - aborting, nothing was changed.")
        return 4, {}

    srcEntries, dstEntries = srcScan.entries, dstScan.entries

    # Safety net: an empty (e.g. unmounted) source must not wipe the target
    if not srcEntries and dstEntries and not allowEmpty:
        logging.critical("Source is empty but target is not. Refusing to wipe the target "
                         "(use --allow-empty if this is intended).")
        return 5, {}

    # Never delete anything in places we could not read or deliberately skipped
    protectedKeys = set(srcScan.unreadable)
    protectedKeys.update(k for k, v in srcEntries.items() if v[0] == "link")

    stats = dict(copied=0, updated=0, deleted=0, mkdirs=0, renamed=0,
                 skipped=0, errors=srcScan.errors + dstScan.errors)
    tag = "[DRY-RUN] " if dryRun else ""

    # --- Copy / update (parents always come before their children) ---
    for key, (kind, size, mtime, rel) in srcEntries.items():
        srcPath = src / rel
        dstPath = dst / rel
        dstInfo = dstEntries.get(key)
        try:
            if kind == "link":
                logging.info(f"SKIP (symlink/junction not followed): {srcPath}")
                stats["skipped"] += 1
                continue

            # Same name, different upper/lower case -> rename in target
            if dstInfo is not None:
                dstLeaf = dstInfo[3].rsplit("/", 1)[-1]
                srcLeaf = rel.rsplit("/", 1)[-1]
                if dstLeaf != srcLeaf:
                    logging.info(f"{tag}RENAME {dstPath.parent / dstLeaf} -> {srcLeaf}")
                    if not dryRun:
                        (dstPath.parent / dstLeaf).rename(dstPath)
                    stats["renamed"] += 1

            if kind == "dir":
                if dstInfo is None:
                    logging.info(f"{tag}MKDIR {dstPath}")
                    if not dryRun:
                        dstPath.mkdir(parents=True, exist_ok=True)
                    stats["mkdirs"] += 1
                elif dstInfo[0] != "dir":
                    logging.info(f"{tag}REPLACE (file/link -> directory) {dstPath}")
                    if not dryRun:
                        removeFileOrLink(dstPath)
                        dstPath.mkdir(parents=True, exist_ok=True)
                    stats["mkdirs"] += 1

            elif kind == "file":
                if dstInfo is None:
                    logging.info(f"{tag}COPY {srcPath} -> {dstPath}")
                    if not dryRun:
                        ensureParent(dstPath)
                        copyFile(srcPath, dstPath)
                    stats["copied"] += 1
                elif dstInfo[0] != "file":
                    logging.info(f"{tag}REPLACE (directory/link -> file) {dstPath}")
                    if not dryRun:
                        if dstInfo[0] == "dir":
                            removeTree(dstPath)
                        else:
                            removeFileOrLink(dstPath)
                        ensureParent(dstPath)
                        copyFile(srcPath, dstPath)
                    stats["updated"] += 1
                else:
                    _, dsize, dmtime, _ = dstInfo
                    if size != dsize or abs(mtime - dmtime) > MTIME_TOLERANCE:
                        logging.info(f"{tag}UPDATE {srcPath} -> {dstPath}")
                        if not dryRun:
                            copyFile(srcPath, dstPath)
                        stats["updated"] += 1
        except OSError as e:
            stats["errors"] += 1
            logging.warning(f"SKIP (error: {e}): {srcPath}")

    # --- Delete (children before parents) ---
    for key, (kind, _s, _m, rel) in sorted(dstEntries.items(), reverse=True):
        if key in srcEntries or isProtected(key, protectedKeys):
            continue
        dstPath = dst / rel
        try:
            if not os.path.lexists(dstPath):
                continue                # already gone (e.g. removed with parent)
            if kind in ("file", "link"):
                logging.info(f"{tag}DELETE {dstPath}")
                if not dryRun:
                    removeFileOrLink(dstPath)
                stats["deleted"] += 1
            elif kind == "dir":
                if dryRun:
                    logging.info(f"{tag}RMDIR {dstPath}")
                    stats["deleted"] += 1
                else:
                    makeWritable(dstPath)
                    if not any(dstPath.iterdir()):
                        logging.info(f"RMDIR {dstPath}")
                        dstPath.rmdir()
                        stats["deleted"] += 1
                    else:
                        logging.warning(f"SKIP (directory not empty): {dstPath}")
                        stats["skipped"] += 1
        except OSError as e:
            stats["errors"] += 1
            logging.warning(f"SKIP (locked/read-only: {e}): {dstPath}")

    logging.info(f"{tag}Sync finished: {stats['copied']} copied, {stats['updated']} updated, "
                 f"{stats['deleted']} deleted, {stats['mkdirs']} directories created, "
                 f"{stats['renamed']} renamed, {stats['skipped']} skipped, "
                 f"{stats['errors']} errors")
    return (7 if stats["errors"] else 0), stats

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        sys.exit(1)

def main():
    parser = _Parser(description="One-way mirror: makes <target> an exact copy of <source>. "
                                 "Files only present in the target are deleted!")
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--dry-run", action="store_true",
                        help="only log what would be done, change nothing")
    parser.add_argument("--allow-empty", action="store_true",
                        help="allow mirroring an empty source (wipes the target)")
    parser.add_argument("--log-dir", default=None,
                        help="directory for the log file (default: next to the program)")
    args = parser.parse_args()

    logDir = Path(args.log_dir) if args.log_dir else getAppDir()
    logFile = setupLogging(logDir)

    logging.info("=" * 60)
    logging.info(f"syncDisk started | Python {sys.version}")
    logging.info(f"Arguments: {sys.argv}")

    # A trailing backslash before a closing quote ("D:\Data\") leaves a stray quote
    src = Path(args.source.rstrip('"'))
    dst = Path(args.target.rstrip('"'))
    if args.dry_run:
        logging.info("DRY-RUN: nothing will be changed.")

    logging.info(f"Source : {src} (exists: {src.exists()}, isDir: {src.is_dir() if src.exists() else 'n/a'})")
    logging.info(f"Target : {dst} (exists: {dst.exists()})")

    # Actively wake the disks before checking accessibility
    wakeDisk(src)
    wakeDisk(dst)

    if not waitUntilAccessible(src, timeout=60):
        logging.critical(f"Source drive not accessible: {src}")
        sys.exit(3)

    if not waitUntilAccessible(dst, timeout=60):
        logging.critical(f"Target drive not accessible: {dst}")
        sys.exit(4)

    if not src.exists() or not src.is_dir():
        logging.critical(f"Source path does not exist or is not a directory: {src}")
        sys.exit(2)

    # Source and target must be different and must not contain each other
    rs = os.path.normcase(os.path.realpath(src))
    rd = os.path.normcase(os.path.realpath(dst))
    if rs == rd or isInside(rd, rs) or isInside(rs, rd):
        logging.critical(f"Source and target are identical or nested: {rs} <-> {rd}")
        sys.exit(6)

    if dst.exists() and not dst.is_dir():
        logging.critical(f"Target exists but is not a directory: {dst}")
        sys.exit(4)

    if not args.dry_run:
        dst.mkdir(parents=True, exist_ok=True)

    # The log files must never be mirrored (they are open / change during the run)
    skipPaths = []
    if logFile:
        skipPaths = [logFile] + [Path(f"{logFile}.{i}") for i in range(1, LOG_BACKUPS + 1)]
    logging.info(f"Starting sync: {src} -> {dst}")
    code, _ = sync(src, dst, dryRun=args.dry_run, allowEmpty=args.allow_empty,
                   skipPaths=skipPaths)
    logging.info(f"syncDisk finished (exit code {code}).")
    sys.exit(code)

if __name__ == "__main__":
    main()
