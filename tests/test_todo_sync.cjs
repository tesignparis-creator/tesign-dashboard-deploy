/* Run with: node tests/test_todo_sync.cjs */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const KEY = 'tesign.todo.v1';
const files = ['dashboard.html', 'enzo.html'];
const blocks = files.map(file => {
  const html = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  return html.slice(html.indexOf('    const TODO_KEY'), html.indexOf('    function aggregateMonthly'));
});

function sharedStore(initial, discardWrites = false) {
  const values = new Map(initial === undefined ? [] : [[KEY, initial]]);
  const views = [], pending = [];
  return {
    values,
    connect(onStorage) {
      const storage = {
        getItem(key) { return values.has(key) ? values.get(key) : null; },
        setItem(key, value) {
          if (discardWrites) return;
          const oldValue = storage.getItem(key);
          values.set(key, String(value));
          if (oldValue === String(value)) return;
          for (const view of views) if (view.storage !== storage) {
            pending.push(() => view.onStorage({ key, oldValue, newValue: String(value), storageArea: view.storage }));
          }
        },
      };
      views.push({ storage, onStorage });
      return storage;
    },
    flush() { while (pending.length) pending.shift()(); },
    read() { return JSON.parse(values.get(KEY)); },
  };
}

let viewCount = 0;
function createView(index, store) {
  const listEvents = {}, inputEvents = {}, windowEvents = {};
  const todoList = { innerHTML: '', addEventListener(type, fn) { listEvents[type] = fn; } };
  const todoInput = { value: '', addEventListener(type, fn) { inputEvents[type] = fn; } };
  const todoAdd = {};
  let serial = 0;
  const prefix = `view-${++viewCount}`;
  const context = vm.createContext({
    todoList, todoInput, todoAdd,
    esc: value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]),
    crypto: { randomUUID() { return `${prefix}-${++serial}`; } },
    window: { addEventListener(type, fn) { windowEvents[type] = fn; } },
    localStorage: store.connect(event => windowEvents.storage?.(event)),
  });
  vm.runInContext(blocks[index], context, { filename: files[index] });
  function target(id, className, value) {
    return {
      tagName: value === undefined ? 'BUTTON' : 'TEXTAREA', value,
      closest() { return { dataset: { id } }; },
      classList: { contains(name) { return name === className; } },
    };
  }
  return {
    todoList,
    rows() { return JSON.parse(vm.runInContext('JSON.stringify(todos)', context)); },
    add(text) { todoInput.value = text; todoAdd.onclick(); },
    edit(id, value) { listEvents.input({ target: target(id, '', value) }); },
    cycle(id) { listEvents.click({ target: target(id, 'todo-state') }); },
    remove(id) { listEvents.click({ target: target(id, 'todo-delete') }); },
  };
}

// Both views must share the first set of default IDs, including before any edit.
const store = sharedStore();
const dashboard = createView(0, store), enzo = createView(1, store);
assert.ok(dashboard.rows().length > 0);
assert.deepEqual(enzo.rows(), dashboard.rows());
const firstId = dashboard.rows()[0].id, secondId = dashboard.rows()[1].id;

// Hold storage events to reproduce a stale parent or iframe, then mutate both.
dashboard.edit(firstId, 'Coût complet confirmé');
enzo.add('Action ajoutée depuis Enzo');
dashboard.add('Action ajoutée depuis le tableau actuel');
assert.equal(store.read().find(row => row.id === firstId).text, 'Coût complet confirmé');
assert.ok(store.read().some(row => row.text === 'Action ajoutée depuis Enzo'));
assert.ok(store.read().some(row => row.text === 'Action ajoutée depuis le tableau actuel'));
enzo.cycle(firstId);
assert.equal(store.read().find(row => row.id === firstId).status, 'en cours');
assert.equal(store.read().find(row => row.id === firstId).text, 'Coût complet confirmé');
dashboard.remove(secondId);
enzo.edit(secondId, 'Une modification tardive ne doit pas recréer une tâche supprimée');
assert.ok(!store.read().some(row => row.id === secondId));
store.flush();
assert.deepEqual(dashboard.rows(), store.read());
assert.deepEqual(enzo.rows(), store.read());
assert.match(enzo.todoList.innerHTML, /Coût complet confirmé/);

// A storage event updates the other visible list even without user interaction.
enzo.edit(firstId, 'Modification synchronisée');
assert.doesNotMatch(dashboard.todoList.innerHTML, /Modification synchronisée/);
store.flush();
assert.match(dashboard.todoList.innerHTML, /Modification synchronisée/);

// Deleting every item is an intentional empty list, including in stale views.
for (const row of store.read()) dashboard.remove(row.id);
assert.deepEqual(store.read(), []);
enzo.cycle(firstId);
assert.deepEqual(store.read(), []);
store.flush();
assert.deepEqual(enzo.rows(), []);
assert.equal(enzo.todoList.innerHTML, '');
for (let index = 0; index < files.length; index++) {
  assert.deepEqual(createView(index, store).rows(), []);
  const invalid = sharedStore('{invalid saved data');
  createView(index, invalid);
  assert.equal(invalid.values.get(KEY), '{invalid saved data');

  // A missing key during reread must retain the displayed default IDs.
  const unsaved = createView(index, sharedStore(undefined, true));
  const id = unsaved.rows()[0].id;
  unsaved.edit(id, 'Identifiant initial conservé');
  assert.equal(unsaved.rows()[0].id, id);
  assert.equal(unsaved.rows()[0].text, 'Identifiant initial conservé');
}
console.log('PASS: shared to-do add/edit/status/delete, delayed storage events, empty lists, stable default IDs and invalid-data preservation in both views.');
