# Actual production CDU front evidence

Source `66a08df7` was built successfully and the real Windows in-app browser loaded the recorded entry asset from the 40901 integration backend on 8396. The repository case stayed byte-identical.

The actual main canvas centre Shift-click selected `m_30 CDU_1`, and the entire front cabinet turned green. This verifies whole-model selection on the actual service face with the production build containing the asynchronous texture repaint fix. Its scope is selection/highlight, not aesthetic completion.

For camera visibility, ACU_04 (`m_18`) and the actually raycast opposing Rack_B_07 (`m_19`) were temporarily disabled in UI memory. These checkboxes modify the in-memory project; they were not saved or exported. The saved project was reloaded afterwards: Save was clean, Undo disabled, and the directly mounted `m_18` checkbox was checked again. The archived case SHA remained unchanged.

The unselected front image still shows a pale HMI and coarse coating texture. These observations motivate the next local appearance change; this receipt does not claim that the new appearance is accepted. The default cabinet-interior toggle was off and opacity 100%. Authored external pipes and nearby rack piping remain separate geometry.

The two PNGs are product WebGL canvas captures, not full UI screenshots. CUA separately observed the model identity, modifier action, checkbox, opacity and clean reload. No new scoped console error was observed after the production reload. Existing earlier OV signaling failures are outside this scoped zero-error observation and remain unresolved.

- `front-shift-whole-highlight.png`: actual front whole-model highlight.
- `front-unselected-appearance-before-followup.png`: original front appearance, retained as a comparison.
- `served-build-receipt.json`: build/source/asset bytes; a build is not browser or OV acceptance by itself.
- `observations.json`: exact scope and restoration facts.
