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

        this.applyInitialState(newRow);
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
     * Finds and triggers smart inputs (Binary/Enum/Numeric syncers).
     * @param {HTMLElement} context - The element to search within.
     */
    applyInitialState: function(context = document) {
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

    syncAttribVal: function(select) {
        const { container, fieldName, attr } = this._getBasics(select);
        if (!container) return;
        const val = select.dataset.currentVal || "0";
        
        if (attr && attr.is_binary) {
            const isChecked = parseFloat(val) > 0;
            container.innerHTML = `
                <input type="hidden" name="${fieldName}" value="${isChecked ? '1' : '0'}">
                <input type="checkbox" ${isChecked ? 'checked' : ''} 
                       onchange="this.previousElementSibling.value = this.checked ? '1' : '0'">`;
        } else if (attr && attr.enums) {
            const opts = attr.enums.map((l, i) => 
                `<option value="${i}" ${parseInt(val) === i ? 'selected' : ''}>${l}</option>`).join('');
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
        } else if (attr && attr.enums) {
            const opts = (cur) => attr.enums.map((l, i) => 
                `<option value="${i}" ${parseInt(cur) === i ? 'selected' : ''}>${l}</option>`).join('');
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

document.addEventListener('DOMContentLoaded', () => ConfigEditor.applyInitialState());
