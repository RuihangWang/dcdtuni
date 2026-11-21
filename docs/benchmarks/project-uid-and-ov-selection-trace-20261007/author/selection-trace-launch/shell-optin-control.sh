KIT_COMMAND=(baseline)
if [[ "${DC_CFD_SOURCE_SELECTION_TRACE:-}" == bounded-picks-v1 ]]; then
    KIT_COMMAND+=( --/exts/dc.cfd_bridge/sourceSelectionTrace/enabled=true )
fi
printf '%s\n' "${KIT_COMMAND[@]}"
