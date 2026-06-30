// ══════════════════════════ UC ASSIST PANEL ══════════════════════════

let _ucAssistAvailable = null;  // null=unknown, true/false

function _ucAssistCheckAvail() {
  if (_ucAssistAvailable !== null) return Promise.resolve(_ucAssistAvailable);
  // Effective model = per-panel override, else the Config UC-authoring default.
  const override = _overrideModelBody('ucAssistPanelModelSel');
  const effId = override.model_config_id || _modelDefaults['uc-authoring'];
  _ucAssistAvailable = !!(effId || _reviewModels.some(m => m.enabled));
  const statusEl = document.getElementById('ucAssistStatus');
  if (statusEl) {
    const m = _reviewModels.find(r => r.id === effId);
    statusEl.textContent = m ? _modelKindLabel(m)
      : (_ucAssistAvailable ? 'using default' : 'not configured');
  }
  return Promise.resolve(_ucAssistAvailable);
}

// R4: track prompts the user sends during this Assist session so we can
// stamp them into the UC YAML as a comment header when the user applies a
// suggestion. Reset on panel open so each editing session is its own trail.
let _ucAssistPrompts = [];

function _ucAssistOpen() {
  const panel = document.getElementById('ucAssistPanel');
  panel.style.display = 'flex';
  _populateOverrideSel('ucAssistPanelModelSel', 'uc-authoring');
  _ucAssistAvailable = null; // re-check on open
  _ucAssistCheckAvail();
  _ucAssistPrompts = [];   // reset prompt trail for this session
  // Blank value is the valid "use default" choice now — focus the input.
  document.getElementById('ucAssistInput').focus();
}

// Build a top-of-YAML comment block that preserves the prompts that produced
// the applied content. If the YAML already has an "# UC Assist prompts:" header
// (e.g. from a prior session that was applied + re-edited), replace it. Other
// leading comments are preserved.
function _injectAssistPromptsAsComment(yaml, prompts) {
  if (!prompts || !prompts.length) return yaml;
  const header = '# UC Assist prompts (this UC was iterated from these messages):';
  const block = [
    header,
    ...prompts.map((p, i) => {
      const lines = String(p).split('\n');
      return lines.map((ln, j) =>
        j === 0 ? `#   ${i + 1}. ${ln}` : `#      ${ln}`
      ).join('\n');
    }),
    '#',
  ].join('\n');

  // Strip any existing block from a previous Apply
  const lines = yaml.split('\n');
  let i = 0, end = -1;
  while (i < lines.length && lines[i].startsWith(header)) {
    // Find the end of an existing block (first line not starting with `#` or first blank `#`-only line)
    let j = i + 1;
    while (j < lines.length && lines[j].startsWith('#')) j++;
    end = j;
    i = j;
    break;
  }
  if (end >= 0) {
    return block + '\n' + lines.slice(end).join('\n');
  }
  return block + '\n' + yaml;
}

function _ucAssistClose() {
  const panel = document.getElementById('ucAssistPanel');
  if (panel) panel.style.display = 'none';
}

function _ucAssistAppendMessage(role, text, yaml, error) {
  const hist = document.getElementById('ucAssistHistory');
  const wrap = document.createElement('div');
  wrap.style.cssText = 'display:flex; flex-direction:column; gap:4px;';
  const bubble = document.createElement('div');
  const isUser = role === 'user';
  bubble.style.cssText = `font-size:11px; line-height:1.6; padding:7px 10px; border-radius:3px;
    background:${isUser ? 'var(--accent-bg)' : 'var(--bg-input)'};
    color:${isUser ? 'var(--text)' : 'var(--text)'};
    border:1px solid ${isUser ? 'var(--accent-soft)' : 'var(--border)'};
    align-self:${isUser ? 'flex-end' : 'flex-start'}; max-width:95%;
    ${error ? 'color:var(--red);' : ''}`;
  bubble.textContent = text;
  wrap.appendChild(bubble);
  if (yaml) {
    const applyBtn = document.createElement('button');
    applyBtn.className = 'btn success';
    applyBtn.style.cssText = 'font-size:9px; padding:3px 10px; align-self:flex-start;';
    applyBtn.textContent = '↑ Apply to editor';
    applyBtn.addEventListener('click', () => {
      // R4: stamp the prompts that produced this YAML as a top-of-file
      // comment block so reviewers can see what the author asked for.
      const stamped = _injectAssistPromptsAsComment(yaml, _ucAssistPrompts);
      document.getElementById('ucYamlEditor').value = stamped;
      const titleFromYaml = _extractTitleFromYaml(stamped);
      if (titleFromYaml) document.getElementById('ucNameInput').value = titleFromYaml;
      applyBtn.textContent = '✓ Applied';
      applyBtn.disabled = true;
    });
    wrap.appendChild(applyBtn);
  }
  hist.appendChild(wrap);
  hist.scrollTop = hist.scrollHeight;
}

async function _ucAssistSend() {
  const input = document.getElementById('ucAssistInput');
  const msg = (input.value || '').trim();
  if (!msg) return;
  const avail = await _ucAssistCheckAvail();
  if (!avail) {
    _ucAssistAppendMessage('assistant', 'No model selected. Use the selector at the top of this panel to pick a model, then try again.', null, true);
    document.getElementById('ucAssistPanelModelSel').focus();
    return;
  }
  _ucAssistPrompts.push(msg);   // R4: record for later YAML stamping
  _ucAssistAppendMessage('user', msg);
  input.value = '';
  const btn = document.getElementById('ucAssistSendBtn');
  btn.disabled = true; btn.textContent = '…';
  const currentYaml = document.getElementById('ucYamlEditor').value || '';
  try {
    const ucPayload = { message: msg, current_yaml: currentYaml,
                        ..._overrideModelBody('ucAssistPanelModelSel') };
    const resp = await api('/api/uc-assist', {
      method: 'POST',
      body: JSON.stringify(ucPayload),
    });
    const explanation = resp.explanation || (resp.error ? `Error: ${resp.error}` : '(no response)');
    _ucAssistAppendMessage('assistant', explanation, resp.yaml_suggestion || null, !!resp.error);
  } catch(e) {
    _ucAssistAppendMessage('assistant', `Request failed: ${e.message}`, null, true);
  } finally {
    btn.disabled = false; btn.textContent = 'Send';
  }
}

document.getElementById('ucAssistToggleBtn').addEventListener('click', () => {
  const panel = document.getElementById('ucAssistPanel');
  if (panel.style.display === 'none' || !panel.style.display) {
    _ucAssistOpen();
    document.getElementById('ucAssistToggleBtn').style.color = 'var(--accent)';
  } else {
    _ucAssistClose();
    document.getElementById('ucAssistToggleBtn').style.color = '';
  }
});

document.getElementById('ucAssistSendBtn').addEventListener('click', _ucAssistSend);
document.getElementById('ucAssistInput').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); _ucAssistSend(); }
});
document.getElementById('ucAssistClearBtn').addEventListener('click', () => {
  document.getElementById('ucAssistHistory').innerHTML = '';
  document.getElementById('ucAssistInput').value = '';
});
async function saveUC() {
  const btn = document.getElementById('saveUCModal'), status = document.getElementById('ucModalStatus');
  btn.disabled = true; status.textContent = 'saving…';
  const nameInput = (document.getElementById('ucNameInput').value || '').trim();
  let yamlContent = document.getElementById('ucYamlEditor').value;
  // Sync the Name field into the YAML's top-level title: before persisting,
  // so the editor's two halves never disagree.
  if (nameInput) yamlContent = _injectTitleIntoYaml(yamlContent, nameInput);
  const tags = document.getElementById('ucTagsInput').value.split(',').map(t => t.trim()).filter(Boolean);
  const payload = {yaml_content: yamlContent, tags};
  try {
    let resp;
    if (editingUCId)
      resp = await api(`/api/use-cases/${encodeURIComponent(editingUCId)}`, {method:'PUT', body:JSON.stringify(payload)});
    else
      resp = await api('/api/use-cases', {method:'POST', body:JSON.stringify(payload)});
    toast(`Saved ${resp.uuid}`); closeUCModal(); activeUCId = resp.uuid;
    await loadUCs(); selectUC(resp.uuid);
  } catch (e) {
    // Parse structured 400s from _validate_uc_yaml into a friendlier list
    const vd = (e.status === 400 && e.body)
      && (e.body.detail?.detail === 'uc_validation_failed' ? e.body.detail
          : (e.body.detail === 'uc_validation_failed' ? e.body : null));
    if (vd && vd.errors) {
      status.innerHTML = `<span style="color:var(--red)">${esc(vd.message)}</span>
        <ul style="margin:6px 0 0 16px;padding:0;font-size:11px;color:var(--red);">
          ${vd.errors.map(err => `<li>${esc(err)}</li>`).join('')}
        </ul>`;
    } else {
      status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
    }
    btn.disabled = false;
  }
}

async function validateUC() {
  const status = document.getElementById('ucModalStatus');
  const nameInput = (document.getElementById('ucNameInput').value || '').trim();
  let yamlContent = document.getElementById('ucYamlEditor').value;
  if (nameInput) yamlContent = _injectTitleIntoYaml(yamlContent, nameInput);
  status.textContent = 'validating…'; status.style.color = '';
  try {
    const resp = await api('/api/use-cases/validate', {
      method: 'POST',
      body: JSON.stringify({ yaml_content: yamlContent, tags: [] }),
    });
    if (resp.ok) {
      status.innerHTML = '<span style="color:var(--green)">✓ Validation passed — ready to save.</span>';
    } else {
      status.innerHTML = `<span style="color:var(--red)">⚠ ${resp.errors.length} error${resp.errors.length>1?'s':''}:</span>
        <ul style="margin:6px 0 0 16px;padding:0;font-size:11px;color:var(--red);">
          ${resp.errors.map(err => `<li>${esc(err)}</li>`).join('')}
        </ul>`;
    }
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">Validate failed: ${esc(e.message)}</span>`;
  }
}
document.getElementById('validateUCBtn').addEventListener('click', validateUC);

// #122 — auto-repair the UC being edited (server-side: backfill a missing handle, etc.) and reload.
async function repairUC() {
  const status = document.getElementById('ucModalStatus');
  if (!editingUCId) {
    status.innerHTML = '<span style="color:var(--amber,#d79a2b)">Save the use case first — Repair fixes a saved UC (e.g. backfills a missing handle).</span>';
    return;
  }
  status.textContent = 'repairing…'; status.style.color = '';
  try {
    const r = await api(`/api/use-cases/${encodeURIComponent(editingUCId)}/repair`, { method: 'POST' });
    if (r.yaml_content) document.getElementById('ucYamlEditor').value = r.yaml_content;
    const did = r.repaired || [];
    let html = did.length
      ? `<span style="color:var(--green)">⚕ Repaired: ${did.map(esc).join('; ')}.</span>`
      : `<span style="color:var(--text-dim)">${esc(r.message || 'Nothing to auto-repair.')}</span>`;
    if ((r.remaining_errors || []).length) {
      html += `<ul style="margin:6px 0 0 16px;padding:0;font-size:11px;color:var(--red);">${r.remaining_errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul>`;
    }
    status.innerHTML = html;
    if (did.length) { try { await loadUCs(); } catch (_) {} }   // refresh the list's validity flags
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">Repair failed: ${esc(e.message)}</span>`;
  }
}
document.getElementById('repairUCBtn')?.addEventListener('click', repairUC);

// UC readiness check (DCM feature #4) — advisory definition-quality feedback.
async function checkReadiness() {
  const status = document.getElementById('ucModalStatus');
  const nameInput = (document.getElementById('ucNameInput').value || '').trim();
  let yamlContent = document.getElementById('ucYamlEditor').value;
  if (nameInput) yamlContent = _injectTitleIntoYaml(yamlContent, nameInput);
  status.textContent = 'scoring…'; status.style.color = '';
  try {
    const resp = await api('/api/use-cases/readiness', {
      method: 'POST',
      body: JSON.stringify({ yaml_content: yamlContent, tags: [] }),
    });
    if (!resp.ok) {
      status.innerHTML = `<span style="color:var(--red)">Readiness: ${esc(resp.error || 'could not parse YAML')}</span>`;
      return;
    }
    const band = (resp.band || '').replace('_', ' ');
    const col = READINESS_COLORS[resp.band] || 'var(--text-faint)';
    const failing = (resp.checks || []).filter(c => !c.ok);
    let html = `<span style="color:${col}">⊹ Readiness ${resp.score}/100 (${esc(band)}) — ${resp.passed}/${resp.total} checks pass</span>`;
    if (failing.length) {
      html += `<ul style="margin:6px 0 0 16px;padding:0;font-size:11px;color:var(--text-dim);">`
        + failing.map(c => `<li>${esc(c.hint)}</li>`).join('') + `</ul>`;
    } else {
      html += ` <span style="color:var(--green)">— well-defined.</span>`;
    }
    status.innerHTML = html;
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">Readiness failed: ${esc(e.message)}</span>`;
  }
}
document.getElementById('readinessUCBtn').addEventListener('click', checkReadiness);

const UC_TEMPLATE = `# DAV Use Case — v1.0
title: ""
uuid: uc-<your-uuid-here>
handle: <category>/<descriptor>

scenario:
  description: |
    Describe the scenario in 2-3 sentences.

  actor:
    persona: <persona>
    profile: standard

  intent: |
    What the actor is trying to accomplish.

  success_criteria:
    - First observable success condition
    - Second observable success condition

  dimensions:
    lifecycle_phase: new_request
    resource_complexity: hard_dependencies
    policy_complexity: single_gating
    provider_landscape: multiple_eligible
    governance_context: standard_governance
    failure_mode: happy_path

  profile: standard

  expected_domain_interactions:
    - domain: <domain-name>
      interaction: Description of how this domain is involved

generated_by:
  mode: authoring
  source: human-authored

tags:
  - <tag>
`;
