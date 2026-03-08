import logging
import os
import yaml
from typing import Optional

from ydiskarc.client import YandexDiskClient
from ydiskarc.downloader import ResourceDownloader, format_size


def yd_get_full(
    url: str, output: Optional[str], filename: Optional[str], metadata: bool, verbose: bool = False
) -> None:
    client = YandexDiskClient(verbose=verbose)
    downloader = ResourceDownloader(client, verbose=verbose)

    if output:
        os.makedirs(output, exist_ok=True)

    try:
        resp = client.get_resource_metadata(url)
        metadata_data = resp.json()

        if metadata and output:
            metadata_file = os.path.join(output, "_metadata.json")
            with open(metadata_file, "w", encoding="utf8") as f:
                f.write(resp.text)
            if verbose:
                logging.info(f"Metadata saved to {metadata_file}")
    except Exception as e:
        if verbose:
            logging.error(f"Failed to fetch metadata: {e}")
        # Proceed without metadata
        metadata_data = None

    id = url.rsplit("/", 1)[-1]
    if output is None:
        output = id

    if filename is None:
        filename = "dump.zip"

    resource_type = metadata_data.get("type", "file") if metadata_data else "file"
    filesize = metadata_data.get("size") if metadata_data else None

    if resource_type == "dir":
        try:
            file_count, total_size = scan_directory_for_stats(
                url, "", output, update=False, nofiles=False, verbose=verbose
            )
            size_str = format_size(total_size) if total_size > 0 else "0 B"
            print(f"Total files to download: 1 (ZIP archive containing {file_count} file(s))")
            print(f"Total size: {size_str}")
        except Exception:
            print("Total files to download: 1 (ZIP archive)")
            print("Total size: unknown")
    else:
        if filesize is not None:
            print("Total files to download: 1")
            print(f"Total size: {format_size(filesize)}")
        else:
            print("Total files to download: 1")
            print("Total size: unknown")

    try:
        download_url = client.get_download_link(url)
        downloader.get_file(download_url, filepath=output, filename=filename, filesize=filesize)
    except Exception as e:
        if verbose:
            logging.error(f"Failed to get file: {e}")
        raise ValueError(f"Failed to download resource: {e}")


def scan_directory_for_stats(
    url: str,
    path: str,
    output: str,
    update: bool = True,
    nofiles: bool = False,
    verbose: bool = False,
) -> tuple[int, int]:
    client = YandexDiskClient(verbose=verbose)
    file_count = 0
    total_size = 0

    resp = client.get_resource_metadata(url, path=path, limit=1000)
    data = resp.json()

    if "_embedded" in data and "items" in data["_embedded"]:
        for row in data["_embedded"]["items"]:
            if "path" not in row:
                continue

            if row["type"] == "dir":
                try:
                    sub_count, sub_size = scan_directory_for_stats(
                        url, row["path"], output, update, nofiles, verbose
                    )
                    file_count += sub_count
                    total_size += sub_size
                except Exception as e:
                    if verbose:
                        logging.error(f"Failed to scan subdirectory {row['path']}: {e}")
            elif row["type"] == "file":
                if nofiles:
                    continue

                arr = [output] + [i.rstrip() for i in row["path"].split("/") if i.strip()]
                file_path = os.path.join(*arr[:-1])
                file_name = arr[-1]
                full_file_path = os.path.join(file_path, file_name)

                if os.path.exists(full_file_path) and update:
                    continue

                file_count += 1
                if "size" in row and row["size"] is not None:
                    total_size += row["size"]

    return file_count, total_size


def yd_get_and_store_dir(
    url: str,
    path: str,
    output: str,
    update: bool = True,
    nofiles: bool = False,
    iterative: bool = False,
    verbose: bool = False,
):
    client = YandexDiskClient(verbose=verbose)
    downloader = ResourceDownloader(client, verbose=verbose)

    resp = client.get_resource_metadata(url, path=path, limit=1000)
    data = resp.json()

    arr = [output] + [i.rstrip() for i in path.split("/") if i.strip()]
    dir_path = os.path.join(*arr)
    os.makedirs(dir_path, exist_ok=True)

    metadata_file = os.path.join(dir_path, "_metadata.json")
    with open(metadata_file, "w", encoding="utf8") as f:
        f.write(resp.text)

    if not iterative:
        return data

    if "_embedded" in data and "items" in data["_embedded"]:
        for row in data["_embedded"]["items"]:
            if "path" not in row:
                continue

            if row["type"] == "dir":
                arr = [output] + [i.rstrip() for i in path.split("/") if i.strip()]
                row_path = os.path.join(*arr)
                os.makedirs(row_path, exist_ok=True)

                try:
                    yd_get_and_store_dir(
                        url, row["path"], output, update, nofiles, iterative=True, verbose=verbose
                    )
                except Exception as e:
                    if verbose:
                        logging.error(f"Failed to process subdirectory {row['path']}: {e}")

            elif row["type"] == "file":
                if nofiles:
                    continue

                arr = [output] + [i.rstrip() for i in row["path"].split("/") if i.strip()]
                file_path = os.path.join(*arr[:-1])
                file_name = arr[-1]
                full_file_path = os.path.join(file_path, file_name)

                if os.path.exists(full_file_path) and update:
                    continue

                try:
                    downloader.get_file(
                        row["file"], file_path, filename=file_name, filesize=row.get("size")
                    )
                except Exception as e:
                    if verbose:
                        logging.error(f"Failed to download file {row['path']}: {e}")


class Project:
    """Disk files extractor. Yandex.Disk only right now"""

    def __init__(self) -> None:
        pass

    def configure(self, key: str, projectdir: Optional[str] = None) -> None:
        if projectdir is None:
            projectdir = os.getcwd()
        filepath = os.path.join(projectdir, ".ydiskarc")

        conf = {}
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf8") as f:
                conf = yaml.safe_load(f) or {}

        if "keys" not in conf:
            conf["keys"] = {}
        conf["keys"]["yandex_oauth"] = key

        with open(filepath, "w", encoding="utf8") as f:
            yaml.safe_dump(conf, f)
        logging.info(f"Configuration saved at {filepath}")

    def __store(
        self,
        url: str,
        metapath: str,
        update: bool = False,
        nofiles: bool = False,
        verbose: bool = False,
    ) -> None:
        os.makedirs(metapath, exist_ok=True)

        if not nofiles:
            try:
                file_count, total_size = scan_directory_for_stats(
                    url, "", metapath, update, nofiles, verbose
                )
                if file_count > 0:
                    print(f"Total files to download: {file_count}")
                    print(f"Total size: {format_size(total_size)}")
                elif update:
                    print("All files are already up to date.")
                    return
                else:
                    print("No files found to download.")
                    return
            except Exception as e:
                if verbose:
                    logging.warning(f"Failed to scan directory for stats: {e}")

        yd_get_and_store_dir(
            url, "", metapath, update=update, nofiles=nofiles, iterative=True, verbose=verbose
        )

    def sync(
        self,
        url: str,
        output: str,
        update: bool = False,
        nofiles: bool = False,
        verbose: bool = False,
    ) -> None:
        self.__store(url, output, update, nofiles, verbose)

    def full(
        self,
        url: str,
        output: Optional[str],
        filename: Optional[str],
        metadata: bool,
        verbose: bool = False,
    ) -> None:
        yd_get_full(url, output, filename, metadata, verbose)
