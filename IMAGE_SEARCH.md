# Image search

This extension targets the attached Media Catalog revision
`595f7d4eafecd1c9e6f26bb09d55d501c81e4be7`. It searches existing SQLite thumbnails
using a selected screen region, without opening catalog originals.

## Install

Quit the app completely, including its tray process. In the repository:

```sh
git apply --check media-catalog-image-search.patch
git apply media-catalog-image-search.patch
```

After applying the previous image-search patch, apply the automatic-indexing update:

```sh
git apply --check media-catalog-auto-features.patch
git apply media-catalog-auto-features.patch
```

Then apply the on-demand model-download update:

```sh
git apply --check media-catalog-auto-model.patch
git apply media-catalog-auto-model.patch
```

In the app's active Conda environment:

```sh
conda install --override-channels -c conda-forge opencv
python app.py
```

For pip environments, use `python -m pip install -r requirements.txt`.
For the setup.bat venv, use `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
Install only one OpenCV distribution in an environment: do not combine Conda
opencv with pip OpenCV or install multiple pip OpenCV variants.

## Use

Click **Image search** beside the text search field. The app temporarily hides
its windows. Drag across the desired screen region and release; Esc or
right-click cancels. Search results appear in a separate window, preserving
the main catalog's filters. Screenshots remain in memory only.

Select a result to see its cached page, path, matching page number and ranking
score. Cyan outlines show locally matched regions where available. Copy the
path or use **Select in catalog**, which clears main-window filters and selects
the file without opening it. Explicitly opening the original from the catalog
may download it. Close the search window to return to normal browsing.

- **Standard** uses colour/structure descriptors and SIFT geometric verification;
  no AI model is needed. **AI** uses DINOv2 plus SIFT.
- **Within current catalog filters** restricts results to the files displayed
  when the search window was opened. By default the whole catalog is searched.
- **Thorough partial matching** is on by default and checks local features on
  every candidate page. Turn it off to verify only the top 300 visual candidates.
- At most 100 pages are shown. Geometrically consistent local matches are ranked
  first, followed by visual similarity. Scores are not probabilities, and a
  locally matched image is not proof of the same source file or specimen.
- **Cancel** retains completed index entries. The next search resumes indexing.
- **Rebuild search index** rebuilds only the selected engine's search data,
  without regenerating thumbnails or reading originals.

### Automatic feature extraction

- At startup, the app checks missing features for both engines and shows page
  counts in a Yes/No confirmation (default No). For the existing backlog, model
  download/inference starts only after Yes. When the model is missing, all cached pages are counted for DINO;
  actual existing feature entries are rechecked after the download.
- No defers the existing backlog until another launch or an explicitly started
  search. It does not prevent automatic indexing of newly generated thumbnails.
- Successfully saved new/regenerated thumbnails enqueue all their cached pages
  for both Standard and DINO, regardless of the selected search engine. Completed
  features are reused. Only the newly saved files enter this automatic queue.
- DINO requires installed packages. Missing models are downloaded on demand:
  after startup consent, during new-thumbnail indexing, or for an AI search.
  Complete local models are reused without network access. Missing packages or
  broken models defer DINO while Standard can still run. Python packages are
  never installed automatically; restart after installing them.
- The main window shows status and **Cancel feature extraction**. Cancellation
  retains completed entries; subsequent new thumbnails still enqueue work.
  Thumbnail-generation pause and feature-extraction cancellation are separate.
- An explicitly started search also fills missing features for its selected
  engine, even if startup indexing was declined. Corrupt thumbnail errors are
  recorded, avoiding repeated startup prompts; use Rebuild to retry them.

Indexing reads only SQLite thumbnails and local model files, never originals.

## Optional AI engine

Activate the app's Conda environment and install the optional packages:

```sh
conda install --override-channels -c conda-forge "pytorch-cpu>=2.2,<3" "transformers>=4.40,<5" "safetensors>=0.4,<1" "huggingface_hub>=0.23,<1"
```

Alternatively, for pip environments (use `.venv\Scripts\python.exe` for the venv):

```sh
python -m pip install "torch>=2.2,<3" --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-image-ai.txt
```

Models are downloaded automatically when needed. The default destination is
`models/dinov2-small`; a custom folder saved in the search dialog takes precedence.
You can still run `python download_search_model.py` to pre-download the model.
Model weights occupy about 88 MB; runtime libraries
require additional space. The engine uses CPU; a GPU is not required. You can
copy a downloaded model directory from another PC. A download status is shown;
cancel or quit terminates the downloader. Failure/cancellation defers further
automatic attempts for that session; restart or start an AI search to retry.
Incomplete files are retained for retry, and never loaded as a complete model.
Download timeout is 15 minutes. Downloads contact Hugging Face's model servers;
images, thumbnails and the database are never uploaded. Loading is local-only, safetensors-only,
with remote Python code disabled. The download script pins revision
`ed25f3a31f01632728cabb09d1542f84ab7b0056` of
[facebook/dinov2-small](https://huggingface.co/facebook/dinov2-small)
(Apache License 2.0).

## Limits and storage

Search data is stored in the existing SQLite database, with registration ID,
thumbnail revision, page, engine version and AI model fingerprint. Startup after
confirmation, successful thumbnail generation, and explicit searches update
new/changed pages in a worker thread. Standard and AI caches are
separate. Full backup/restore includes the index; compact annotation export
does not. Adding the schema does not queue thumbnail regeneration.

Only cached pages are searched: up to 20 pages per multipage file, and only the
saved representative frame of a video. Missing thumbnails are counted and
skipped, never fetched. Corrupt cached pages are counted; use Rebuild to retry.
Old thumbnails pending regeneration and missing source files can still be
searched, with their status shown. Fine details lost at thumbnail resolution
cannot be recovered. Whole-image and four overlapping region features improve
cropped-image search but cannot guarantee arbitrary crop recall. Repeated cell
patterns, severe edits or tiny pictures inside slides can give poor matches.
Nearest candidates are returned even when the desired image is absent.

Large catalogs take more time and space, especially with thorough matching.
Cancellation/exit can wait for the current image operation to finish.
The existing automatic thumbnail generator is independent and may still
download originals while search is running. Disable automatic generation and
wait for active jobs to finish if you need the entire app to stop retrieval.

Tests cover offline source access, cropped/resized/rotated images, cached pages,
incremental indexing, cancellation, lifecycle races, backup, mixed-DPI coordinate
composition, selection gestures, GUI navigation and shutdown. Development QA
also covers startup consent/refusal, new generation after refusal, both engines,
missing/broken DINO, and shutdown with an unanswered confirmation. QA
uses Linux/headless Qt; actual Windows monitors and Box Drive require testing
on the target PC.

```sh
python -m unittest discover -s tests -p "test_*.py"
python tests/smoke_image_search.py
```

Set `MEDIA_CATALOG_TEST_DINO` to the local model directory to enable the optional
offline real-model test.
Set `MEDIA_CATALOG_TEST_DOWNLOAD=1` to enable the actual network download test.
