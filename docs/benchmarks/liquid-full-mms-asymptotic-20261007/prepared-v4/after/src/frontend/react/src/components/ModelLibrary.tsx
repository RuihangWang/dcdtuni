import type { CfdProject, Construction } from '../schema/cfdproj'
import {
  addInstanceFromTemplate,
  libraryTemplates,
  pinModelToLibrary,
  unpinTemplate,
  type LibraryKind,
} from '../geometry/modelLibrary'
import { useTranslation } from '../useTranslation'

/**
 * The two halves of the model library, in the two panels the legacy puts them.
 *
 * `ModelLibraryPin` is the rack and ACU inspector's 固定入库 group
 * (STL_generate.html:8754 and :9300); `LibraryAddButtons` is the row of
 * `+ <label>` buttons the creation toolbar grows underneath the create buttons
 * (:12310). They are one file because they are one feature: the button in the
 * toolbar only exists because someone pressed the button in the inspector.
 *
 * Colours are the toolbar's per-kind accents, rack amber and ACU cyan, so a
 * template button reads as the same kind as the create button above it.
 */

const SECTION: React.CSSProperties = {
  borderTop: '1px solid #444',
  marginTop: 6,
  paddingTop: 6,
}

const TITLE: React.CSSProperties = {
  fontSize: 10,
  color: '#aaa',
  padding: '2px 4px',
  marginBottom: 4,
}

const KIND_STYLE: Record<LibraryKind, React.CSSProperties> = {
  rack: {
    background: 'rgba(255,184,0,0.06)',
    borderColor: 'rgba(255,184,0,0.35)',
    color: '#ffb800',
  },
  acu: {
    background: 'rgba(0,212,255,0.06)',
    borderColor: 'rgba(0,212,255,0.35)',
    color: 'var(--accent3)',
  },
}

export interface LibraryAddButtonsProps {
  project: CfdProject | null
  /** Called with the new model's uid, so the caller can select it. */
  onCreated: (uid: string) => void
}

export function LibraryAddButtons({ project, onCreated }: LibraryAddButtonsProps) {
  const { t } = useTranslation()
  const templates = libraryTemplates(project)
  // The whole section disappears with the last template, as in the legacy: an
  // empty "add from library" header reads as a broken panel.
  if (templates.length === 0) return null

  return (
    <div className="lib-add-wrap" id="creation-library-section" style={SECTION}>
      <div style={TITLE}>{t('library.add_from_library')}</div>
      {templates.map((template) => (
        <div key={template.id} style={{ display: 'flex', gap: 4, alignItems: 'stretch' }}>
          <button
            className="btn"
            data-template-id={template.id}
            style={{ ...KIND_STYLE[template.kind], flex: 1, margin: 0, marginBottom: 4 }}
            onClick={() => {
              const uid = addInstanceFromTemplate(template.kind, template.id)
              if (uid) onCreated(uid)
            }}
          >
            + {template.label}
          </button>
          {/* Not in the legacy, which offers no way at all to remove a template
              once pinned — the row grows for ever and undo is the only recourse.
              A pin made by accident should not be permanent. */}
          <button
            className="node-action-btn"
            data-unpin-template-id={template.id}
            title={t('tooltip.delete')}
            onClick={() => unpinTemplate(template.kind, template.id)}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}

export interface ModelLibraryPinProps {
  model: Construction
}

/**
 * The inspector's pin group. Shown for racks and ACUs only.
 *
 * The label is prompted for BEFORE the edit, and a cancel returns without one:
 * the legacy does the same (STL_generate.html:13949) so that backing out of the
 * prompt leaves no orphaned undo step to press through.
 */
export function ModelLibraryPin({ model }: ModelLibraryPinProps) {
  const { t } = useTranslation()
  if (model.kind !== 'rack' && model.kind !== 'acu') return null

  return (
    <div className="prop-group">
      <div className="prop-title">{t('library.title')}</div>
      <button
        className="btn primary"
        id="library-pin-btn"
        style={{ width: '100%' }}
        onClick={() => {
          const fallback = model.meta?.name ?? String(model.kind ?? '')
          const input = window.prompt(t('library.template_label_prompt'), fallback)
          if (input === null) return
          pinModelToLibrary(model.uid, input || fallback)
        }}
      >
        {t('library.pin_btn')}
      </button>
      <div className="prop-hint">{t('library.pin_hint')}</div>
    </div>
  )
}
