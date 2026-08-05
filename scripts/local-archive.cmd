@echo off
rem Local archiver: a COMMITTING WRITER into the archive of record (the
rem repo at origin/main) - never a silent fork. Order matters: pull first
rem so the union sees the cloud rows, poll, commit only archive files,
rem push. autostash protects unrelated mid-work edits; the union merge
rem driver (.gitattributes) resolves same-day two-writer appends; a push
rem race is retried once via pull --rebase. A failed push leaves the
rem commit local for the next run to carry.
cd /d C:\Users\Calvin\Documents\Project\MicoNBA
git pull --rebase --autostash origin main
C:\ProgramData\anaconda3\python.exe -m mironba.data.ingest.archive
git add archive/rss
git diff --cached --quiet || git commit -m "data(archive): local scheduled poll"
git pull --rebase --autostash origin main
git push origin main
