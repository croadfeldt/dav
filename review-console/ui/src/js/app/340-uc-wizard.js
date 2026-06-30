// ══════════════════════════ UC WIZARD (M12b / ADR-008) ══════════════════════════
//
// 5-step guided creation: scenario → generate → review → assign → save.
// State is wholly local to the modal (no globals beyond _wizardState) so
// re-opening starts fresh. Reuses /api/uc-assist for steps 2+3 refinement
// and the same POST /api/use-cases + /api/sets/{id}/members endpoints as
// M12a for the actual persistence.

let _wizardState = null;   // { step, scenario, context, modelResolved, yaml, explanation, tags[], setMode, setNewName, setExistingId, savedUcUuid }

// M12 — show / hide a busy overlay over an element while an LLM call is in flight.
// Returns a function that hides the overlay; call it in a finally{} block.
function _showBusy(parentEl, msg) {
  if (!parentEl) return () => {};
  parentEl.classList.add('wz-busy');
  const overlay = document.createElement('div');
  overlay.className = 'wz-busy-msg';
  overlay.innerHTML = `<span class="llm-spinner"></span><span>${esc(msg || 'working…')}</span>`;
  parentEl.appendChild(overlay);
  return () => {
    parentEl.classList.remove('wz-busy');
    overlay.remove();
  };
}

function openUcWizard() {
  document.getElementById('ucWizardModal').classList.add('open');
  _wizardState = {
    step: 1,
    scenario: '',
    context: '',
    modelResolved: null,
    yaml: '',
    explanation: '',
    tags: [],
    setMode: 'skip',
    setNewName: '',
    setExistingId: null,
    savedUcUuid: null,
  };
  document.getElementById('wzScenario').value = '';
  document.getElementById('wzContext').value = '';
  document.getElementById('wzYaml').value = '';
  document.getElementById('wzYamlReview').value = '';
  document.getElementById('wzYamlFinal').value = '';
  document.getElementById('wzRefineInput').value = '';
  document.getElementById('wzTags').value = '';
  document.getElementById('wzSetNewName').value = '';
  document.getElementById('wzExplanation').style.display = 'none';
  document.getElementById('wzValidateStatus').textContent = '';
  document.getElementById('wzStatus').textContent = '';
  _populateOverrideSel('wzModelSel', 'uc-authoring');
  wzShowStep(1);
}

function closeUcWizard() {
  document.getElementById('ucWizardModal').classList.remove('open');
  _wizardState = null;
}

function wzShowStep(n) {
  _wizardState.step = n;
  document.querySelectorAll('#ucWizardModal .wz-pane').forEach(p => {
    p.style.display = (parseInt(p.dataset.step, 10) === n) ? '' : 'none';
  });
  document.querySelectorAll('#ucWizardModal .wz-step').forEach(s => {
    const sn = parseInt(s.dataset.step, 10);
    s.classList.remove('active', 'done');
    if (sn === n) s.classList.add('active');
    else if (sn < n) s.classList.add('done');
  });
  // Back button visibility
  document.getElementById('wzBackBtn').style.display = (n > 1) ? '' : 'none';
  // Primary button label
  const primary = document.getElementById('wzPrimaryBtn');
  const labels = {1:'Next: Generate →', 2:'Next: Review →', 3:'Next: Assign →', 4:'Next: Save →', 5:'Save use case'};
  primary.textContent = labels[n];
  primary.disabled = false;
  document.getElementById('wzStatus').textContent = '';
  // Step-specific entry hooks
  if (n === 2) wzEnterStep2();
  else if (n === 3) wzEnterStep3();
  else if (n === 4) wzEnterStep4();
  else if (n === 5) wzEnterStep5();
}

async function wzNext() {
  const n = _wizardState.step;
  if (n === 1) {
    const scenario = document.getElementById('wzScenario').value.trim();
    if (!scenario) { toast('Describe the scenario first', true); return; }
    _wizardState.scenario = scenario;
    _wizardState.context = document.getElementById('wzContext').value.trim();
    // Blank ⇒ use the Config UC-authoring default; else the chosen override.
    _wizardState.modelResolved = _overrideModelBody('wzModelSel');
    wzShowStep(2);
  } else if (n === 2) {
    const yaml = document.getElementById('wzYaml').value.trim();
    if (!yaml) { toast('Generate or paste a draft first', true); return; }
    _wizardState.yaml = yaml;
    wzShowStep(3);
  } else if (n === 3) {
    _wizardState.yaml = document.getElementById('wzYamlReview').value;
    wzShowStep(4);
  } else if (n === 4) {
    _wizardState.tags = document.getElementById('wzTags').value
      .split(',').map(t => t.trim()).filter(Boolean);
    _wizardState.setMode = document.querySelector('input[name="wzSetMode"]:checked').value;
    _wizardState.setNewName = document.getElementById('wzSetNewName').value.trim();
    const sv = document.getElementById('wzSetExistingSel').value;
    _wizardState.setExistingId = sv ? parseInt(sv, 10) : null;
    wzShowStep(5);
  } else if (n === 5) {
    await wzFinalSave();
  }
}

function wzBack() {
  if (_wizardState.step > 1) wzShowStep(_wizardState.step - 1);
}

// ── Step 2: auto-generate if YAML is empty ─────────────────────────────────────
async function wzEnterStep2() {
  if (_wizardState.yaml) {
    document.getElementById('wzYaml').value = _wizardState.yaml;
    if (_wizardState.explanation) {
      const ex = document.getElementById('wzExplanation');
      ex.textContent = _wizardState.explanation;
      ex.style.display = '';
    }
    return;
  }
  await wzGenerate();
}

async function wzGenerate() {
  const status = document.getElementById('wzGenStatus');
  status.innerHTML = '<span class="llm-spinner"></span>generating draft…';
  const primary = document.getElementById('wzPrimaryBtn');
  primary.disabled = true;
  const pane = document.querySelector('#ucWizardModal .wz-pane[data-step="2"]');
  const hideBusy = _showBusy(pane, 'Generating UC draft via LLM — this can take 30–90s for larger models…');
  try {
    const payload = {
      message: _wizardState.scenario,
      context: _wizardState.context || null,
      ...(_wizardState.modelResolved || {}),
    };
    const resp = await api('/api/uc-assist', {method:'POST', body:JSON.stringify(payload)});
    if (resp.yaml_suggestion) {
      _wizardState.yaml = resp.yaml_suggestion;
      document.getElementById('wzYaml').value = resp.yaml_suggestion;
    }
    _wizardState.explanation = resp.explanation || '';
    if (resp.explanation) {
      const ex = document.getElementById('wzExplanation');
      ex.textContent = resp.explanation;
      ex.style.display = '';
    }
    status.textContent = '';
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
  } finally {
    hideBusy();
    primary.disabled = false;
  }
}

async function wzRefine() {
  const req = document.getElementById('wzRefineInput').value.trim();
  if (!req) { toast('Type a refinement request first', true); return; }
  const current = document.getElementById('wzYaml').value.trim();
  if (!current) { toast('Generate a draft first', true); return; }
  const status = document.getElementById('wzGenStatus');
  status.innerHTML = '<span class="llm-spinner"></span>refining…';
  const btn = document.getElementById('wzRefineBtn');
  btn.disabled = true;
  const pane = document.querySelector('#ucWizardModal .wz-pane[data-step="2"]');
  const hideBusy = _showBusy(pane, 'Asking the LLM to refine the draft…');
  try {
    const payload = {
      message: req,
      current_yaml: current,
      context: _wizardState.context || null,
      ...(_wizardState.modelResolved || {}),
    };
    const resp = await api('/api/uc-assist', {method:'POST', body:JSON.stringify(payload)});
    if (resp.yaml_suggestion) {
      _wizardState.yaml = resp.yaml_suggestion;
      document.getElementById('wzYaml').value = resp.yaml_suggestion;
    }
    if (resp.explanation) {
      const ex = document.getElementById('wzExplanation');
      ex.textContent = resp.explanation;
      ex.style.display = '';
      _wizardState.explanation = resp.explanation;
    }
    document.getElementById('wzRefineInput').value = '';
    status.textContent = '';
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
  } finally {
    hideBusy();
    btn.disabled = false;
  }
}

// ── Step 3: parsed fields preview + editable YAML ─────────────────────────────
function wzEnterStep3() {
  document.getElementById('wzYamlReview').value = _wizardState.yaml || '';
  wzRenderParsedPreview();
  document.getElementById('wzYamlReview').oninput = () => {
    _wizardState.yaml = document.getElementById('wzYamlReview').value;
    wzRenderParsedPreview();
  };
}

function wzRenderParsedPreview() {
  const yaml = _wizardState.yaml || '';
  const fields = {
    title:             /^title:\s*(.+)$/m,
    uuid:              /^uuid:\s*(.+)$/m,
    handle:            /^handle:\s*(.+)$/m,
    lifecycle_phase:   /lifecycle_phase:\s*(\w+)/,
    failure_mode:      /failure_mode:\s*(\w+)/,
    profile:           /^\s*profile:\s*(\w+)/m,
    'generated_by.mode':   /^\s*mode:\s*(\w+)/m,
    'generated_by.source': /^\s*source:\s*([\w-]+)/m,
  };
  const rows = Object.entries(fields).map(([k, re]) => {
    const m = re.exec(yaml);
    const val = m ? m[1].trim().replace(/^["']|["']$/g, '') : '<em style="color:var(--text-faint);">unset</em>';
    return `<div style="display:flex; gap:8px; padding:2px 0;"><span style="min-width:180px; color:var(--text-faint); font-family:var(--mono,monospace);">${esc(k)}</span><span style="color:var(--text);">${val}</span></div>`;
  }).join('');
  document.getElementById('wzParsed').innerHTML =
    '<div style="font-size:10px; text-transform:uppercase; letter-spacing:0.10em; color:var(--text-faint); margin-bottom:4px;">parsed fields</div>' + rows;
}

async function wzValidate() {
  const status = document.getElementById('wzValidateStatus');
  status.innerHTML = '<span class="llm-spinner"></span>validating…';
  try {
    const resp = await api('/api/use-cases/validate', {
      method:'POST',
      body: JSON.stringify({yaml_content: document.getElementById('wzYamlReview').value, tags: []}),
    });
    if (resp.ok) {
      status.innerHTML = '<span style="color:var(--green)">✓ valid</span>';
    } else {
      const errs = (resp.errors || []).join('; ');
      status.innerHTML = `<span style="color:var(--red)">✗ ${esc(errs || 'invalid')}</span>`;
    }
  } catch (e) {
    // Endpoint returns 400 with structured errors on invalid YAML
    let detail = e.message;
    try {
      const parsed = JSON.parse(e.message.replace(/^[^{]*/, ''));
      if (parsed.errors) detail = parsed.errors.join('; ');
    } catch(_) {}
    status.innerHTML = `<span style="color:var(--red)">${esc(detail)}</span>`;
  }
}

// ── Step 4: tags + Set assignment ─────────────────────────────────────────────
async function wzEnterStep4() {
  // Pre-fill tags from YAML
  const yaml = _wizardState.yaml || '';
  const tagsMatch = /^tags:\s*\n((?:\s+-\s+.+\n?)+)/m.exec(yaml);
  let tags = [];
  if (tagsMatch) {
    tags = tagsMatch[1].split('\n').map(l => {
      const t = /^\s+-\s+(.+)$/.exec(l);
      return t ? t[1].trim().replace(/^["']|["']$/g, '') : '';
    }).filter(Boolean);
  }
  if (!_wizardState.tags.length) _wizardState.tags = tags;
  document.getElementById('wzTags').value = _wizardState.tags.join(', ');
  // Populate existing Scoping Sets dropdown
  if (!allSets || !allSets.length) { try { await loadSets(); } catch(_) {} }
  const sel = document.getElementById('wzSetExistingSel');
  sel.innerHTML = '<option value="">— pick a set —</option>' +
    (allSets || []).filter(s => s.id !== ALL_SET_ID).map(s => `<option value="${s.id}">${esc(s.name)}${s.is_default?' (default)':''}</option>`).join('');
}

// ── Step 5: summary + save ────────────────────────────────────────────────────
function wzEnterStep5() {
  document.getElementById('wzYamlFinal').value = _wizardState.yaml || '';
  const titleMatch = /^title:\s*(.+)$/m.exec(_wizardState.yaml || '');
  const title = titleMatch ? titleMatch[1].trim().replace(/^["']|["']$/g, '') : '(untitled)';
  let setDest = 'Skip — leave unassigned';
  if (_wizardState.setMode === 'new') setDest = `Create new Set "${_wizardState.setNewName || '(name missing)'}"`;
  else if (_wizardState.setMode === 'existing') {
    const found = (allSets || []).find(s => s.id === _wizardState.setExistingId);
    setDest = `Add to existing Set "${found ? found.name : '(none picked)'}"`;
  }
  document.getElementById('wzSummary').innerHTML = `
    <div style="font-size:10px; text-transform:uppercase; letter-spacing:0.10em; color:var(--text-faint); margin-bottom:4px;">ready to save</div>
    <div style="font-size:13px; font-weight:600; margin-bottom:4px;">${esc(title)}</div>
    <div style="font-size:11px; color:var(--text-dim);">tags: ${_wizardState.tags.length ? _wizardState.tags.map(esc).join(', ') : '<em>none</em>'}</div>
    <div style="font-size:11px; color:var(--text-dim);">destination: ${esc(setDest)}</div>`;
}

async function wzFinalSave() {
  const primary = document.getElementById('wzPrimaryBtn');
  primary.disabled = true;
  const status = document.getElementById('wzStatus');
  status.innerHTML = '<span class="llm-spinner"></span>saving UC…';
  const pane = document.querySelector('#ucWizardModal .wz-pane[data-step="5"]');
  const hideBusy = _showBusy(pane, 'Persisting UC + Set assignment…');
  try {
    const resp = await api('/api/use-cases', {
      method:'POST',
      body: JSON.stringify({yaml_content: _wizardState.yaml, tags: _wizardState.tags}),
    });
    _wizardState.savedUcUuid = resp.uuid || resp.use_case?.uuid;
    // Set assignment
    let targetSetId = null;
    if (_wizardState.setMode === 'new') {
      if (!_wizardState.setNewName) { toast('New Scoping Set needs a name', true); primary.disabled = false; status.textContent = ''; return; }
      status.textContent = 'creating set…';
      const created = await api('/api/sets', {method:'POST', body:JSON.stringify({name: _wizardState.setNewName, description: 'created from UC wizard'})});
      targetSetId = created.id;
    } else if (_wizardState.setMode === 'existing') {
      targetSetId = _wizardState.setExistingId;
    }
    if (targetSetId) {
      status.textContent = 'adding to set…';
      await api(`/api/sets/${targetSetId}/members`, {
        method:'POST',
        body: JSON.stringify({uc_uuid: _wizardState.savedUcUuid, uc_source: 'managed'}),
      });
    }
    toast(`UC saved: ${_wizardState.savedUcUuid}`);
    closeUcWizard();
    try { await loadUCs(); } catch(_) {}
    try { await loadSets(); } catch(_) {}
  } catch (e) {
    let detail = e.message;
    try {
      const parsed = JSON.parse(e.message.replace(/^[^{]*/, ''));
      if (parsed.errors) detail = 'validation: ' + parsed.errors.join('; ');
      else if (parsed.message) detail = parsed.message;
    } catch(_) {}
    status.innerHTML = `<span style="color:var(--red)">${esc(detail)}</span>`;
    primary.disabled = false;
  } finally {
    hideBusy();
  }
}

// Hand off the in-progress wizard state to the legacy ucModal for power-user edits
function wzToAdvanced() {
  const yaml = document.getElementById('wzYamlReview').value
    || document.getElementById('wzYaml').value
    || _wizardState?.yaml || '';
  const tags = (_wizardState?.tags || []).join(', ');
  closeUcWizard();
  openUCModal(null, yaml, tags);
}
