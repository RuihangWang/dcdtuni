# Default intent effective-mode display

A missing or marker-only Newton experiment showed editable duration/load-step controls while the top bar was steady because the Panel waited for a saved DAE block before displaying its effective mode. The compiler already resolved the marked intent correctly and rejected steady events; the UI could invite a setting that Prepare then refused.

The correction is one pure expression: a Newton intent reads the existing domain mode before materialization. It does not compile a graph, save an experiment, alter migration, reset a timeline or change physical inputs. Three real Panel regressions cover missing and marker-only intent through steady→transient, plus saved unmarked v2 control compatibility. The two new default branches fail on the old source; all 71 related tests and TypeScript then pass.

The previous full-suite result remains 3526 pass / one optional fixture-dump skip at the prior source. It is not presented as a fresh full run of this follow-up. All 52 previous evidence members are byte-identical. Before/after source archives and the baseline's newly added tests are preserved here; receipt and manifest bind the exact bytes. Root owns build, commit and real browser acceptance.

Time contract is unchanged: an old no-experiment v4 source can migrate its saved air end time and uniform initial fields into the shared timeline. Materializing the experiment later does not overwrite that timeline. The current canonical explicit experiment migrates its saved 180 s and steady initialization. An already existing shared timeline remains authoritative until a normal user edit changes it.
