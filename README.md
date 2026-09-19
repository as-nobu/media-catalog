# Media Catalog

[日本語](README.ja.md) · English

A Windows app for browsing images, videos, TIFFs, PowerPoint, SVG and PDF-compatible Illustrator files as a local thumbnail catalog.

## Get started

1. Install **Python 3.12 (64-bit)** with the Windows Python launcher.
2. Download and extract the app, then open the folder containing `app.py`.
3. Run `setup.bat`, then `start.bat`.
4. Select **Add folder** and choose the files you want to catalog.

Video thumbnails require **FFmpeg** on PATH. PowerPoint thumbnails require **Microsoft PowerPoint for Windows**.

### Anaconda / Miniconda

Use Anaconda Prompt in the app folder instead of `setup.bat`:

AI previews use pypdfium2; QtPdf is no longer required. Existing environments: run `conda install -c conda-forge "pypdfium2>=4.30,<6"`, restart the app, then regenerate failed AI previews.

```shell
conda create -n media-catalog --override-channels -c conda-forge python=3.12 "pyside6>=6.7,<7" "pillow>=10.4,<13" "numpy>=1.26,<3" "msoffcrypto-tool>=5.4,<6" "pywin32>=306" "pypdfium2>=4.30,<6" ffmpeg
conda activate media-catalog
python app.py
```

All dependencies are installed with Conda. Do not use `setup.bat` / `start.bat` for this environment; those use a separate venv. For an existing environment, activate it first (for example, `conda activate qt_test`), then run:

```shell
conda install --override-channels -c conda-forge "pyside6>=6.7,<7" "pillow>=10.4,<13" "numpy>=1.26,<3" "msoffcrypto-tool>=5.4,<6" "pywin32>=306" "pypdfium2>=4.30,<6" ffmpeg
```

### Start at Windows sign-in without a console (Conda)

1. Confirm `python app.py` works in Anaconda Prompt. Find the Conda installation with `conda info --base` and the activated environment with `echo %CONDA_PREFIX%`.
2. Save this as `start-hidden.vbs` in the app folder, adjusting all three paths. In Notepad, select “All files”; use UTF-16 LE when paths contain Japanese characters.

```vbscript
Set sh = CreateObject("WScript.Shell")
q = Chr(34)
condaBat = "C:\Users\YourName\miniconda3\condabin\conda.bat"
envDir = "C:\Users\YourName\miniconda3\envs\media-catalog"
appDir = "C:\Users\YourName\Documents\media_catalog"
sh.CurrentDirectory = appDir
command = q & sh.ExpandEnvironmentStrings("%ComSpec%") & q & " /d /s /c " & q & _
    q & condaBat & q & " activate " & q & envDir & q & " && " & _
    q & envDir & "\pythonw.exe" & q & " " & q & appDir & "\app.py" & q & q
sh.Run command, 0, False
```

3. Double-click the VBS to test it. It activates Conda and launches `pythonw.exe` without a console. **The application's main window still opens.**
4. Press `Win+R`, enter `shell:startup`, and place a shortcut to the VBS there. It will run at your next Windows sign-in. Remove the shortcut to disable startup.

If startup fails, run `python app.py` in Anaconda Prompt to see the error. This method requires Windows Script Host / VBScript to be enabled. Update the VBS if paths change. The app does not register itself for startup.

References: [Conda activation](https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html), [Windows startup settings](https://support.microsoft.com/en-us/windows/configure-startup-applications-in-windows-115a420a-0bff-4a6f-90e0-1934c844e473).

## Everyday use

| Action | How |
|---|---|
| Change language | Choose **日本語 / English** at the top. The setting is remembered. |
| Browse a folder | Select it in the left tree; optionally include subfolders. |
| Preview pages | Hover over a thumbnail. The first 20 TIFF / PowerPoint pages are generated, cached and tiled. The total page count is retained. |
| Open a file | Double-click it, or use the right-click menu. |
| Find files | Search filenames, notes and tags; filter favorites, missing entries, errors or timeouts. |
| Add notes and tags | Edit the right-hand panel. Separate tags with commas. |
| Show or resize details | Toggle **Details**, or drag the panel divider. |
| Pause generation | Select **Pause generation**. Active jobs finish; folder scans continue. |
| Retry a file | Right-click and choose **Rescan / regenerate**. Auto-generation must be on and resumed. |
| Back up | Select **Back up database** and save to a new file. |
| Quit | Right-click the tray icon and select **Quit**. Closing the window normally hides it in the tray. |

Notes, tags and favorites are saved after one second without further edits, and when you switch files, hide the app in the tray, quit, or start a backup. Language switching preserves unsaved edits.

## Cloud folders and your files

**Auto-generation is on by default.** Generating a thumbnail may download an uncached Box Drive file. Turn auto-generation off before adding a folder if you only want to list its files. Browsing saved thumbnails does not download the originals.

Scans compare file size and modification time. This assumes your cloud client can provide that information without downloading file contents. Changes made on another PC are detected after they reach the local cloud client.

The app does not modify or delete original files. Missing files are marked for review; removing catalog entries or unregistering a folder also removes their saved notes, tags and thumbnails. Opening an original in another app allows editing there.

## Import and search

**Move a full catalog to another PC:** create a database backup on the source PC, then choose **Settings → Restore full database** on the destination. Review each **Path on this PC**, select **Restore and exit**, and restart after the app exits. Thumbnails, notes, tags and favorites are retained. Periodic scans and auto-generation start OFF; check paths before enabling them.

**Change paths in the current catalog:** use **Settings → Relocate catalog folders**. Original folders/files are not moved. Both operations create a separate local database copy and retain the previous database; its location is shown at completion. To return to it, select it via **Restore full database**. **Open database folder** shows the active database location.

To transfer notes and tags, open **Settings → Import notes and tags** and choose a database saved with **Back up database** on the other PC. Map source/local folders if paths differ, then preview changes. Tags are combined without duplicates; conflicting notes have a per-file choice. Before applying, a database backup is saved under `backups` in the data folder. Original files, the source database, favorites and thumbnails are unchanged.

Search supports space-separated AND terms, `"phrases"`, and `-exclusions`. Scope terms with `name:`, `memo:`, `tag:`, `path:`, `meta:`, `ext:`, or `type:` (example: `cells tag:experiment -failed ext:tif`). Format and sorting controls sit beside search; generation options are under **Settings**. Press `Ctrl+F` to focus search.

## Troubleshooting

- **SVG / Illustrator:** SVG and PDF-compatible AI are supported. AI previews use up to 20 saved PDF pages without launching Illustrator or opening linked assets. Legacy / non-PDF-compatible AI is unsupported. Missing content cannot be recovered. SVG with external references is skipped; embed images instead. Fonts and effects may differ from the authoring application.
- **Microscope TIFF:** integer and floating-point grayscale images use per-page brightness normalization for previews, not quantitative measurement. Original data is unchanged.
- **Password-protected PowerPoint:** confirmed encryption is skipped. Unknown encryption status proceeds to generation; a password dialog may appear. Zero-byte images and presentations are cataloged as empty files without an error.
- **FFmpeg not found:** activate the environment used to run the app, run `conda install -c conda-forge ffmpeg`, then restart and retry. For a separate installation, add FFmpeg to PATH or set `MEDIA_CATALOG_FFMPEG` to its executable.
- **Large image:** the default limit is **300 megapixels per page**. Large images also need sufficient RAM. Set Maximum pixels under Settings to 1–2000 MP. The value is stored in the database and applies to the next generation job. Manually regenerate files that previously failed.
- **Timeout:** the default is 30 minutes. Increase it for slow downloads, then retry the affected files.
- **Backup:** use the app's backup command while it is running. Backups include the catalog and thumbnails, not original files. To restore, fully quit, set aside the current data folder, and place the backup there as `catalog.sqlite3`; do not reuse old `-wal` or `-shm` files.
- **Data location:** a local per-user MediaCatalog folder under `%LOCALAPPDATA%`. Updating the app keeps the catalog.

Native Windows dialogs and messages from external applications may follow the OS language. Windows / Box Drive / PowerPoint behavior is not fully tested on the release build environment.

## License

[MIT License](LICENSE). External applications and dependencies retain their own licenses.

Files with generation errors or timeouts are excluded from automatic generation, including after restart, periodic scans, or file changes. File listing metadata still updates. To retry, select the files and use Regenerate selected or the context menu regeneration command.


TIFF scale: reads standard X/YResolution and ResolutionUnit tags, and OME-TIFF PhysicalSizeX/Y (preferred). Details show µm/pixel and the source. Toggle hover scale bars in Settings. Regenerate existing TIFF previews to collect calibration. Standard tags may represent print DPI rather than specimen calibration. Proprietary vendor tags are not supported.
