---
name: media-reading
description: "Guideline for reading outside sources (video, web pages, PDFs, books) for a note or a check on a machine other sessions share: one heavy job at a time, a memory ceiling, stream instead of loading whole, untrusted downloads, and how to cite each kind. Load before you read a video, web page, PDF or book for a source."
---

# Reading video, web pages, PDFs and books

Scope: any lane that reads an outside source for a note or a check, on a machine shared with other sessions. The machine rules exist because several sessions decoding video at once can fill memory and swap until every session on the machine stalls.

## Machine rules (every kind)

1. One heavy job at a time (video decode, speech-to-text, OCR batch, PDF layout, simulations). Start the next only after the last one exits.
2. Ceiling about 1.5 GB per job. Check a new command once on a short sample before the full run: `/usr/bin/time -l <cmd>` on macOS or `/usr/bin/time -v <cmd>` on Linux (maximum resident set size); on Windows, watch the process in Task Manager.
3. Stream, never slurp: read frames from an `ffmpeg` pipe one at a time and keep only the few you need. Never capture a whole video's output in memory, never stack all frames in one array, and crop and downscale in `ffmpeg` (`-vf crop=…,scale=…`) before your own code sees a pixel.
4. Background jobs: note the PID when you start one, stop only that PID, and check it is gone before you report. Never stop processes by a name pattern (`pkill -f`, `killall`, `taskkill /IM`).
5. Run tools with `uvx` or `uv run --no-project --with <pkg>` (or a throwaway virtual environment), never install into the system Python. Downloads and outputs go to a temp folder of your own (`/tmp/<task>/`, `%TEMP%\<task>\`), deleted at wrap; keep only the quoted extract in the repo.
6. Downloaded files are untrusted: put each in its own empty folder, keep your scripts in another, and run Python on them with `-I`.

## Video

- Talk text, in this order: the platform's own transcript (`youtube-transcript-api`) → subtitles only (`uvx yt-dlp --skip-download --write-auto-subs --sub-langs en <url>`) → the audio alone and a local speech-to-text model.
- For the audio route, download only the part you need (`uvx yt-dlp -f bestaudio --download-sections "*MM:SS-MM:SS" -o <tmp>/a.%(ext)s <url>`) and transcribe it with a small Whisper model (for example `uvx --from faster-whisper` or `openai-whisper` with `small` or smaller; larger models pass the ceiling). Work in sections of a few minutes, not the whole talk.
- Video sites block caption fetches per IP for hours (HTTP 429 and the like). Don't retry in a loop: take the audio route or the creator's own article, and note the source as "retry later".
- Speech-to-text misspells names. Check every name you quote against the title, the slides or the description.
- Frames: one every N seconds `-vf "select='isnan(prev_selected_t)+gte(t-prev_selected_t,N)',scale=480:-2" -fps_mode vfr f_%03d.jpg`; scene cuts `select='gt(scene,0.3)',showinfo` (read `pts_time`). In a talk, crop to the slide area first or use interval frames; cut detection on the whole frame finds nothing.
- Measuring motion: download only the seconds you need, pipe small grey or RGB frames, and process them as they arrive.
- Cite: title, speaker, URL, date, and the `m:ss` timestamp from the transcript or the frame's `pts_time`.

## Web pages

- Read with the web tools you have. To keep a page as markdown for quoting: `uvx trafilatura --markdown -u <url>` (body only, small).
- A dead or changed page: `curl "https://archive.org/wayback/available?url=<url>"` and cite the snapshot URL with its date.
- Pages behind a login or heavy JavaScript: use the logged-in browser tool the workspace has, if any, rather than starting a new headless browser.
- Cite: title, author, URL, published date, the date you fetched it, and the heading or paragraph you quote.

## PDFs and books

- Text PDF: `pymupdf4llm.to_markdown(path, page_chunks=True)` (through `uv run --with pymupdf4llm`) and write a `<!-- page N -->` marker per chunk, so every quote has a page. Check the page count first; some PDFs are one-page "moved" stubs.
- Scanned page or image: render only the pages you quote (`pymupdf` `get_pixmap(dpi=200)`) and OCR them with a local engine (for example `tesseract`, from your package manager).
- EPUB: `uvx --from 'markitdown[all]' markitdown book.epub` (keeps chapter headings, no pages). Cite chapter and section; for a printed book cite ISBN and page from the print or a PDF.
- Don't use markitdown for PDFs (it drops page numbers) or web pages (it keeps the menus). Leave heavy layout models (several GB) alone unless the light tools fail on a table you need.

## Done check

- Every quote has its timestamp, page or section, and its source line (title, author or speaker, URL or ISBN, date).
- Your temp folder is deleted, no PID of yours is still running, and nothing was added to the system Python.
