/**
 * Generic Dynamic Row Utilities for Configuration Pages
 */

const ConfigEditor = {
    /**
     * Adds a row from a <template> to a container.
     * @param {string} containerId - ID of the list container.
     * @param {string} templateId - ID of the <template> element.
     * @param {Function} onAfterAdd - Optional callback to initialize new elements (like selects).
     */
    addRow: function(containerId, templateId, customCallback = null) {
        console.log("Adding row to:", containerId, "using template:", templateId);
        const list = document.getElementById(containerId);
        const templateEl = document.getElementById(templateId);
        const template = templateEl.innerHTML;
        const index = Date.now();
        const html = template.replace(/__INDEX__/g, index);

        list.insertAdjacentHTML('beforeend', html);
        const newRow = list.lastElementChild;

        list.classList.remove('hidden'); // Show the list
        const emptyMsg = document.getElementById(containerId + '-empty');
        if (emptyMsg) emptyMsg.classList.add('hidden'); // Hide the "No items" text

        // AUTO-INIT: If the row has an attribute selector, sync it immediately
        const attrSelect = newRow.querySelector('select[data-is-attribute="true"]');
        if (attrSelect) {
            this.syncAttributeInput(attrSelect);
        }

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
     * Shared logic for Binary (Boolean) vs Numeric attribute inputs.
     * Used in Character, Item, and Location editors.
     */
    syncAttributeInput: function(selectElement) {
        const row = selectElement.closest('.col-row') || selectElement.parentElement;
        const selectedOption = selectElement.options[selectElement.selectedIndex];
        const isBinary = selectedOption.dataset.isBinary === 'True' || selectedOption.dataset.isBinary === 'true';
        
        // Find existing inputs
        const textInput = row.querySelector('input[type="text"]');
        const numInput = row.querySelector('input[type="number"]');
        const checkbox = row.querySelector('input[type="checkbox"]');
        const hiddenField = row.querySelector('input[type="hidden"]');
        const label = row.querySelector('label.checkbox-label');
        
        // We target whichever input is currently holding the "value"
        const activeInput = textInput || numInput || hiddenField;
        if (!activeInput) return;

        const name = activeInput.name;
        const currentValue = activeInput.value;

        if (isBinary) {
            if (activeInput.type !== 'hidden') {
                // Switch to Binary UI (Checkbox)
                const newHidden = document.createElement('input');
                newHidden.type = 'hidden';
                newHidden.name = name;
                newHidden.value = (currentValue == '1' || currentValue == '1.0') ? '1' : '0';

                const newLabel = document.createElement('label');
                newLabel.className = 'checkbox-label';
                const newCheckbox = document.createElement('input');
                newCheckbox.type = 'checkbox';
                newCheckbox.checked = newHidden.value === '1';
                newCheckbox.onchange = () => { newHidden.value = newCheckbox.checked ? '1' : '0'; };
                
                newLabel.appendChild(newCheckbox);
                activeInput.replaceWith(newHidden);
                newHidden.parentElement.insertBefore(newLabel, newHidden.nextSibling);
            }
        } else {
            if (checkbox) {
                // Switch to Numeric UI (Text/Number input)
                const newInput = document.createElement('input');
                newInput.type = 'text';
                newInput.name = name;
                newInput.value = currentValue;
                newInput.placeholder = 'Value';

                if (hiddenField) hiddenField.remove();
                if (label) label.replaceWith(newInput);
            }
        }
    }
};
