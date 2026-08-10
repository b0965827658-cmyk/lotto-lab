(function savedNumberLifecycle(global) {
  'use strict';

  const STORAGE_KEY = 'star-saved-number-records-v1';
  const DEVICE_KEY = 'star-saved-number-device-v1';
  const MIGRATION_KEY = 'star-saved-number-migration-v1';
  const STATUS = Object.freeze({
    ACTIVE: 'ACTIVE',
    WAITING_DRAW: 'WAITING_DRAW',
    SETTLED: 'SETTLED',
    ARCHIVED: 'ARCHIVED',
    HIDDEN: 'HIDDEN'
  });

  const nowIso = () => new Date().toISOString();
  const normalizeNumbers = (numbers) => [...new Set((numbers || []).map(Number).filter((number) => Number.isInteger(number) && number >= 1 && number <= 39))].sort((a, b) => a - b);
  const randomId = (prefix) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const parse = (value, fallback) => { try { return JSON.parse(value); } catch { return fallback; } };

  function deviceId() {
    let value = localStorage.getItem(DEVICE_KEY);
    if (!value) {
      value = global.crypto?.randomUUID?.() || randomId('device');
      localStorage.setItem(DEVICE_KEY, value);
    }
    return value;
  }

  function load() {
    const records = parse(localStorage.getItem(STORAGE_KEY) || '[]', []);
    return Array.isArray(records) ? records : [];
  }

  function persist(records) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
    return records;
  }

  function save(input) {
    const records = load();
    const numbers = normalizeNumbers(input.numbers);
    if (!numbers.length) return { ok: false, reason: 'EMPTY_NUMBERS' };
    const lottery = String(input.lottery || '');
    const targetDrawId = String(input.target_draw_id || '');
    const duplicate = records.find((record) => !record.hidden_at && record.device_id === deviceId() && record.lottery === lottery && String(record.target_draw_id || '') === targetDrawId && normalizeNumbers(record.numbers).join(',') === numbers.join(','));
    if (duplicate) return { ok: false, reason: 'DUPLICATE', record: duplicate };
    const record = {
      record_id: randomId('saved'),
      user_id: null,
      device_id: deviceId(),
      lottery,
      target_draw_id: targetDrawId,
      numbers,
      created_at: input.created_at || nowIso(),
      status: targetDrawId ? STATUS.WAITING_DRAW : STATUS.ARCHIVED,
      actual_numbers: [],
      matched_numbers: [],
      hit_count: null,
      settled_at: null,
      archived_at: targetDrawId ? null : nowIso(),
      hidden_at: null,
      migration_status: input.migration_status || null
    };
    records.unshift(record);
    persist(records);
    return { ok: true, record };
  }

  function removeActive(recordId) {
    const records = load();
    const index = records.findIndex((record) => record.record_id === recordId);
    if (index < 0) return false;
    if (![STATUS.ACTIVE, STATUS.WAITING_DRAW].includes(records[index].status)) return false;
    records.splice(index, 1);
    persist(records);
    return true;
  }

  function updateActive(recordId, numbers) {
    const records = load();
    const record = records.find((item) => item.record_id === recordId);
    if (!record || ![STATUS.ACTIVE, STATUS.WAITING_DRAW].includes(record.status)) return { ok: false, reason: 'NOT_EDITABLE' };
    const normalized = normalizeNumbers(numbers);
    if (!normalized.length) return { ok: false, reason: 'EMPTY_NUMBERS' };
    const duplicate = records.find((item) => item.record_id !== recordId && !item.hidden_at && item.device_id === record.device_id && item.lottery === record.lottery && String(item.target_draw_id || '') === String(record.target_draw_id || '') && normalizeNumbers(item.numbers).join(',') === normalized.join(','));
    if (duplicate) return { ok: false, reason: 'DUPLICATE' };
    record.numbers = normalized;
    persist(records);
    return { ok: true, record };
  }

  function hide(recordId) {
    const records = load();
    const record = records.find((item) => item.record_id === recordId);
    if (!record || [STATUS.ACTIVE, STATUS.WAITING_DRAW].includes(record.status)) return false;
    record.status = STATUS.HIDDEN;
    record.hidden_at = nowIso();
    persist(records);
    return true;
  }

  function reconcile(lottery, officialDraws) {
    const rows = (officialDraws || []).map((row) => ({ ...row, numbers: normalizeNumbers(row.numbers) }));
    const latest = rows[0];
    const records = load();
    let changed = false;
    records.forEach((record) => {
      if (record.lottery !== lottery || ![STATUS.ACTIVE, STATUS.WAITING_DRAW, STATUS.SETTLED].includes(record.status)) return;
      if ([STATUS.ACTIVE, STATUS.WAITING_DRAW].includes(record.status)) {
        const draw = rows.find((row) => String(row.period || row.draw_id || row.date || '') === String(record.target_draw_id || ''));
        if (!draw) return;
        const actual = normalizeNumbers(draw.numbers);
        const chosen = new Set(normalizeNumbers(record.numbers));
        record.actual_numbers = actual;
        record.matched_numbers = actual.filter((number) => chosen.has(number));
        record.hit_count = record.matched_numbers.length;
        record.settled_at = nowIso();
        record.status = STATUS.SETTLED;
        changed = true;
      }
      if (record.status === STATUS.SETTLED && latest && String(latest.period || latest.draw_id || latest.date || '') !== String(record.target_draw_id || '')) {
        record.status = STATUS.ARCHIVED;
        record.archived_at = nowIso();
        changed = true;
      }
    });
    if (changed) persist(records);
    return records;
  }

  function migrateLegacy() {
    if (localStorage.getItem(MIGRATION_KEY) === 'complete') return { migrated: 0 };
    let migrated = 0;
    const legacyArrays = [
      ['star-picks-tw', 'tw539'],
      ['star-picks-f5', 'ca-fantasy5']
    ];
    legacyArrays.forEach(([key, lottery]) => {
      const numbers = parse(localStorage.getItem(key) || '[]', []);
      if (Array.isArray(numbers) && numbers.length) {
        const result = save({ lottery, target_draw_id: '', numbers, migration_status: 'LEGACY_UNASSIGNED' });
        if (result.ok) migrated += 1;
      }
    });
    const oldRecords = parse(localStorage.getItem('lotto-lab-saved-picks') || '[]', []);
    if (Array.isArray(oldRecords)) oldRecords.forEach((record) => {
      const result = save({ lottery: record.game || 'tw539', target_draw_id: '', numbers: record.numbers || record.pick, created_at: record.createdAt, migration_status: 'LEGACY_UNASSIGNED' });
      if (result.ok) migrated += 1;
    });
    localStorage.setItem(MIGRATION_KEY, 'complete');
    return { migrated };
  }

  global.StarSavedNumbers = Object.freeze({
    STORAGE_KEY, DEVICE_KEY, MIGRATION_KEY, STATUS,
    load, save, removeActive, updateActive, hide, reconcile, migrateLegacy,
    normalizeNumbers, deviceId
  });
})(window);
