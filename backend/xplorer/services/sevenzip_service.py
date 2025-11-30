"""
7-Zip integration service.
Provides archive operations using 7-Zip if installed.
"""

import os
import subprocess
import logging
import asyncio
import tempfile
from typing import Any
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="7zip_")

# Common 7-Zip installation paths
SEVENZIP_PATHS = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\7-Zip\7z.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\7-Zip\7z.exe"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%\7-Zip\7z.exe"),
]

# Archive extensions that 7-Zip can open
ARCHIVE_EXTENSIONS = {
    '.7z', '.zip', '.rar', '.tar', '.gz', '.bz2', '.xz',
    '.cab', '.iso', '.wim', '.arj', '.lzh', '.lzma',
}


class SevenZipService:
    """Service for 7-Zip operations."""

    _sevenzip_path: str | None = None
    _checked: bool = False

    @classmethod
    def _find_sevenzip(cls) -> str | None:
        """Find 7-Zip executable path."""
        if cls._checked:
            return cls._sevenzip_path

        cls._checked = True

        # Check common paths
        for path in SEVENZIP_PATHS:
            expanded = os.path.expandvars(path)
            if os.path.isfile(expanded):
                cls._sevenzip_path = expanded
                logger.info(f"Found 7-Zip at: {expanded}")
                return cls._sevenzip_path

        # Try to find in PATH
        try:
            result = subprocess.run(
                ["where", "7z.exe"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            if result.returncode == 0 and result.stdout.strip():
                cls._sevenzip_path = result.stdout.strip().split('\n')[0]
                logger.info(f"Found 7-Zip in PATH: {cls._sevenzip_path}")
                return cls._sevenzip_path
        except Exception as e:
            logger.debug(f"Failed to find 7-Zip in PATH: {e}")

        logger.info("7-Zip not found on system")
        return None

    @staticmethod
    async def is_installed() -> dict[str, Any]:
        """Check if 7-Zip is installed."""
        loop = asyncio.get_event_loop()

        def check():
            path = SevenZipService._find_sevenzip()
            return {
                "installed": path is not None,
                "path": path,
            }

        return await loop.run_in_executor(_executor, check)

    @staticmethod
    def _is_archive(path: str) -> bool:
        """Check if a file is an archive."""
        ext = os.path.splitext(path)[1].lower()
        return ext in ARCHIVE_EXTENSIONS

    @staticmethod
    async def add_to_archive(
        paths: list[str],
        archive_path: str,
        format: str = "zip"
    ) -> dict[str, Any]:
        """
        Add files/folders to an archive.

        Args:
            paths: List of file/folder paths to add
            archive_path: Path for the output archive
            format: Archive format (zip, 7z)

        Returns:
            Result dict with success status
        """
        loop = asyncio.get_event_loop()

        def do_add():
            sevenzip = SevenZipService._find_sevenzip()
            if not sevenzip:
                return {"success": False, "error": "7-Zip not installed"}

            try:
                # Build command
                # 7z a <archive> <files...>
                cmd = [sevenzip, "a", "-y"]

                # Set format
                if format == "7z":
                    cmd.append("-t7z")
                else:
                    cmd.append("-tzip")

                cmd.append(archive_path)
                cmd.extend(paths)

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minute timeout
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )

                if result.returncode == 0:
                    return {
                        "success": True,
                        "archivePath": archive_path,
                    }
                else:
                    return {
                        "success": False,
                        "error": result.stderr or "Archive creation failed",
                    }

            except subprocess.TimeoutExpired:
                return {"success": False, "error": "Operation timed out"}
            except Exception as e:
                logger.error(f"Error creating archive: {e}")
                return {"success": False, "error": str(e)}

        return await loop.run_in_executor(_executor, do_add)

    @staticmethod
    async def show_add_to_archive_dialog(paths: list[str]) -> dict[str, Any]:
        """
        Open 7-Zip's "Add to Archive" dialog for user configuration.

        Args:
            paths: List of file/folder paths to add

        Returns:
            Result dict with success status
        """
        loop = asyncio.get_event_loop()

        def do_show_dialog():
            sevenzip = SevenZipService._find_sevenzip()
            if not sevenzip:
                return {"success": False, "error": "7-Zip not installed"}

            if not paths:
                return {"success": False, "error": "No paths provided"}

            list_file_path = None
            try:
                # Get 7-Zip GUI path (7zG.exe in same directory)
                sevenzip_dir = os.path.dirname(sevenzip)
                sevenzip_gui = os.path.join(sevenzip_dir, "7zG.exe")

                if not os.path.isfile(sevenzip_gui):
                    return {"success": False, "error": "7-Zip GUI not found"}

                # Create a temporary file to store the list of paths
                # This is more robust than passing many paths on the command line
                with tempfile.NamedTemporaryFile(
                    mode='w',
                    encoding='utf-8',
                    delete=False,
                    suffix='.txt'
                ) as list_file:
                    list_file.write('\n'.join(paths))
                    list_file_path = list_file.name
                
                logger.debug(f"7-Zip list file created at: {list_file_path}")

                # The "a" command with the -ad switch forces the GUI dialog.
                # A dummy archive name must be provided for the syntax to be valid.
                # Using a list file (@filename) is robust for many files/folders.
                cmd = [sevenzip_gui, "a", "archive.zip", "-ad", f"@{list_file_path}"]

                # The working directory should be the common parent of all paths
                # to ensure relative paths are handled correctly if they exist.
                # For simplicity with absolute paths, setting it to the parent
                # of the first file is a reasonable default.
                work_dir = os.path.dirname(paths[0])

                # Use Popen to not wait for the dialog to close.
                # We can't easily delete the temp file here because the 7zG.exe
                # process is detached and might read it later.
                subprocess.Popen(
                    cmd,
                    cwd=work_dir,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
                )

                return {"success": True}

            except Exception as e:
                logger.error(f"Error opening add to archive dialog: {e}")
                # Clean up the temp file on error if it was created
                if list_file_path and os.path.exists(list_file_path):
                    os.unlink(list_file_path)
                return {"success": False, "error": str(e)}

        return await loop.run_in_executor(_executor, do_show_dialog)

    @staticmethod
    async def open_archive(path: str) -> dict[str, Any]:
        """
        Open an archive with 7-Zip File Manager.

        Args:
            path: Path to the archive

        Returns:
            Result dict with success status
        """
        loop = asyncio.get_event_loop()

        def do_open():
            sevenzip = SevenZipService._find_sevenzip()
            if not sevenzip:
                return {"success": False, "error": "7-Zip not installed"}

            try:
                # Get 7-Zip File Manager path (7zFM.exe in same directory)
                sevenzip_dir = os.path.dirname(sevenzip)
                file_manager = os.path.join(sevenzip_dir, "7zFM.exe")

                if not os.path.isfile(file_manager):
                    # Fall back to opening with 7z.exe
                    file_manager = sevenzip

                # Use subprocess.Popen to not wait for the process
                subprocess.Popen(
                    [file_manager, path],
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if os.name == 'nt' else 0
                )

                return {"success": True, "path": path}

            except Exception as e:
                logger.error(f"Error opening archive: {e}")
                return {"success": False, "error": str(e)}

        return await loop.run_in_executor(_executor, do_open)

    @staticmethod
    async def extract_archive(
        archive_path: str,
        destination: str | None = None
    ) -> dict[str, Any]:
        """
        Extract an archive.

        Args:
            archive_path: Path to the archive
            destination: Destination directory (default: same as archive)

        Returns:
            Result dict with success status
        """
        loop = asyncio.get_event_loop()

        def do_extract():
            sevenzip = SevenZipService._find_sevenzip()
            if not sevenzip:
                return {"success": False, "error": "7-Zip not installed"}

            try:
                # Default destination is archive directory
                if not destination:
                    dest = os.path.dirname(archive_path)
                else:
                    dest = destination

                # Build command
                # 7z x <archive> -o<destination> -y
                cmd = [sevenzip, "x", archive_path, f"-o{dest}", "-y"]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )

                if result.returncode == 0:
                    return {
                        "success": True,
                        "destination": dest,
                    }
                else:
                    return {
                        "success": False,
                        "error": result.stderr or "Extraction failed",
                    }

            except subprocess.TimeoutExpired:
                return {"success": False, "error": "Operation timed out"}
            except Exception as e:
                logger.error(f"Error extracting archive: {e}")
                return {"success": False, "error": str(e)}

        return await loop.run_in_executor(_executor, do_extract)

    @staticmethod
    def get_archive_name(paths: list[str], format: str = "zip") -> str:
        """
        Generate archive name based on input paths.

        Args:
            paths: List of paths to archive
            format: Archive format extension

        Returns:
            Suggested archive filename
        """
        if len(paths) == 1:
            # Single file/folder: use its name
            base_name = os.path.splitext(os.path.basename(paths[0]))[0]
            parent_dir = os.path.dirname(paths[0])
        else:
            # Multiple items: use parent folder name
            parent_dir = os.path.dirname(paths[0])
            base_name = os.path.basename(parent_dir) or "archive"

        return os.path.join(parent_dir, f"{base_name}.{format}")
