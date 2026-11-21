# Liquid result table usability — main 315a25f4

Repeated rack prefixes formerly wrapped onto multiple lines in the narrow
table. It now displays the remaining server name when the exact enclosing
rack prefix matches. Full original names remain in accessible names, hover
titles and curve titles. Other authored names are retained.

Fixed column widths, tabular digits, right-aligned numbers and consistent
spacing improve comparison. In the actual 1280×720 browser viewport all 56
rows retain full name/title identity and all 14 tables fit without horizontal
spill. The four captured before/after rows retain their original displayed
numbers; chip maximum, 61-sample count and 600 s playhead also match.
The first server still opens its correct full-name curve and closing restores
focus. No new console errors occurred; the two prior pointer-lock/offline-OV
errors remain identified in the record.

All 60 related tests and both production builds pass. The 719 tracked
frontend/shared-i18n files match byte for byte. Raw build warnings are retained.
Whole UI screenshots were reviewed inline, with no archived whole-UI file.

This verifies presentation of a saved actual result. It is not another solve,
large air mesh acceptance or a temporal accuracy claim.
