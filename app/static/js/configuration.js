/**
 * Generic Dynamic Row Utilities for Configuration Pages
 */

const ConfigEditor = {
    /**
     * Adds a row from a <template> to a container.
     * @param {string} containerId - ID of the list container.
     * @param {string} templateId - ID of the <template> element.
     * @param {Function} customCallback - Optional callback to initialize new elements (like selects).
     */
    addRow: function(containerId, templateId, customCallback = null) {
        const list = document.getElementById(containerId);
        const templateEl = document.getElementById(templateId);
        const template = templateEl.innerHTML;
        const index = Date.now();
        const html = template.replace(/__INDEX__/g, index);

        list.insertAdjacentHTML('beforeend', html);
        const newRow = list.lastElementChild;

        list.classList.remove('hidden');
        const emptyMsg = document.getElementById(containerId + '-empty');
        if (emptyMsg) emptyMsg.classList.add('hidden');

        this.triggerInitialState(newRow);
        if (customCallback) customCallback(newRow);
    },

    /**
     * Removes a row and shows the 'empty' message if no rows remain.
     */
    removeRow: function(btn, containerId) {
        const row = btn.closest('.col-row') || btn.parentElement;
        row.remove();
        
        const list = document.getElementById(containerId);
        const remainingRows = list.querySelectorAll('.col-row:not(.header-style)');
        if (remainingRows.length === 0) {
            list.classList.add('hidden');
            const emptyMsg = document.getElementById(containerId + '-empty');
            if (emptyMsg) emptyMsg.classList.remove('hidden');
        }
    },

    /**
     * Moves a row up or down and re-sequences the entire list.
     * @param {HTMLElement} btn - The button clicked.
     * @param {number} direction - -1 for Up, 1 for Down.
     * @param {string} containerId - ID of the list container.
     * @param {object} options - { rowSelector, inputClass, displayClass, upClass, downClass }
     */
    moveRow: function(btn, direction, containerId, options = {}) {
        const rowSelector = options.rowSelector || '.col-row';
        const row = btn.closest(rowSelector);
        if (!row) return;

        if (direction === -1 && row.previousElementSibling &&
                !row.previousElementSibling.classList.contains('header-style')) {
            row.parentNode.insertBefore(row, row.previousElementSibling);
        } else if (direction === 1 && row.nextElementSibling) {
            row.parentNode.insertBefore(row.nextElementSibling, row);
        }

        this.renumberRows(containerId, options);
        
        // Keep the moved item in view
        row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },

    /**
     * Resequences order inputs and UI labels for a list.
     */
    renumberRows: function(containerId, options = {}) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const rowSelector = options.rowSelector || '.col-row:not(.header-style)';
        const rows = container.querySelectorAll(rowSelector);

        rows.forEach((row, i) => {
            // 1. Update hidden input
            if (options.inputClass) {
                const input = row.querySelector(options.inputClass);
                if (input) input.value = i;
            }
            // 2. Update # label
            if (options.displayClass) {
                const display = row.querySelector(options.displayClass);
                if (display) display.textContent = `#${i + 1}`;
            }
            // 3. Boundary buttons
            if (options.upClass) {
                const btn = row.querySelector(options.upClass);
                if (btn) btn.disabled = (i === 0);
            }
            if (options.downClass) {
                const btn = row.querySelector(options.downClass);
                if (btn) btn.disabled = (i === rows.length - 1);
            }
        });

        // 4. Handle "No items" message toggle
        if (options.emptyMsgId) {
            const emptyMsg = document.getElementById(options.emptyMsgId);
            if (emptyMsg) emptyMsg.classList.toggle('hidden', rows.length > 0);
        }
    },
    
    /**
     * Finds and triggers smart inputs (Binary/Enum/Numeric syncers).
     * @param {HTMLElement} context - The element to search within.
     */
    triggerInitialState: function(context = document) {
        const selector = 'select[onchange*="ConfigEditor.sync"], select[onchange*="syncFieldAttribState"]';
        context.querySelectorAll(selector).forEach(s => {
            if (typeof s.onchange === 'function') {
                s.onchange();
            }
        });
    },

    /**
     * Handle the dynamic injection of inputs (Checkboxes, Enums, Numbers).
     */

    // --- 1. THE ATTRIBUTE SYNCERS (Smart UI: Binary/Enum/Numeric) ---

    syncAttribVal: function(select, forceNumeric = false) {
        const { container, fieldName, attr } = this._getBasics(select);
        if (!container) return;
        const val = select.dataset.currentVal || "0";
        
        if (attr && attr.is_binary && !forceNumeric) {
            const isChecked = parseFloat(val) > 0;
            container.innerHTML = `
                <input type="hidden" name="${fieldName}" value="${isChecked ? '1' : '0'}">
                <input type="checkbox" ${isChecked ? 'checked' : ''} 
                       onchange="this.previousElementSibling.value = this.checked ? '1' : '0'">`;
        } else if (attr && attr.enums && attr.enum_ids && !forceNumeric) {
            const currentId = parseInt(val);
            const opts = attr.enums.map((l, i) => {
                const id = attr.enum_ids[i];
                return `<option value="${id}" ${currentId === id ? 'selected' : ''}>${l}</option>`;
            }).join('');
            container.innerHTML = `<select name="${fieldName}">${opts}</select>`;
        } else {
            container.innerHTML = `<input type="number" name="${fieldName}" value="${val}" step="any" style="width:10ch;">`;
        }
    },

    syncAttribRange: function(select) {
        const { container, fieldName, attr } = this._getBasics(select);
        if (!container) return;
        const min = select.dataset.currentMin || "";
        const max = select.dataset.currentMax || "";

        if (attr && attr.is_binary) {
            container.innerHTML = `
                <input type="hidden" name="${fieldName}[min]" value="1">
                <input type="hidden" name="${fieldName}[max]" value="1">
                <label class="checkbox-label">Required: <input type="checkbox" checked disabled></label>`;
        } else if (attr && attr.enums && attr.enum_ids) {
            const opts = (cur) => attr.enums.map((l, i) => {
                const id = attr.enum_ids[i];
                return `<option value="${id}" ${parseInt(cur) === id ? 'selected' : ''}>${l}</option>`;
            }).join('');
            container.innerHTML = `
                <span class="label-like">Min:</span> <select name="${fieldName}[min]">${opts(min)}</select>
                <span class="label-like">Max:</span> <select name="${fieldName}[max]">${opts(max)}</select>`;
        } else {
            container.innerHTML = `
                <span class="label-like">Min:</span> <input type="number" name="${fieldName}[min]" value="${min}" step="any" style="width:8ch;" placeholder="-∞">
                <span class="label-like">Max:</span> <input type="number" name="${fieldName}[max]" value="${max}" step="any" style="width:8ch;" placeholder="∞">`;
        }
    },

    // --- 2. THE NUMERIC SYNCERS (Simple UI: Always Numbers) ---

    syncNumVal: function(select) {
        const { container, fieldName } = this._getBasics(select);
        if (!container) return;
        const val = select.dataset.currentVal || "0";
        container.innerHTML = `<input type="number" name="${fieldName}" value="${val}" step="any" style="width:10ch;">`;
    },

    syncNumRange: function(select) {
        const { container, fieldName } = this._getBasics(select);
        if (!container) return;
        const min = select.dataset.currentMin || "";
        const max = select.dataset.currentMax || "";
        container.innerHTML = `
            <span class="label-like">Min:</span> <input type="number" name="${fieldName}[min]" value="${min}" step="any" style="width:8ch;" placeholder="-∞">
            <span class="label-like">Max:</span> <input type="number" name="${fieldName}[max]" value="${max}" step="any" style="width:8ch;" placeholder="∞">`;
    },

    // --- 3. INTERNAL HELPERS ---

    _getBasics: function(select) {
        const wrapper = select.closest('.attrib-wrapper');
        const container = wrapper ? wrapper.querySelector('.attribval-container') : null;
        const fieldName = container ? container.dataset.fieldName : "";
        const attrId = select.value;
        const attr = (typeof ATTRIB_REGISTRY !== 'undefined' && attrId) ? ATTRIB_REGISTRY[attrId] : null;
        return { container, fieldName, attr };
    },
};

document.addEventListener('DOMContentLoaded', () => ConfigEditor.triggerInitialState());
