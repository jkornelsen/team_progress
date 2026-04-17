
/**
 * Wrapper GET method
 */
async function apiGet(url, errorContext) {
    const options = { method: 'GET' };
    if (errorContext) {
        options.errorContext = errorContext;
    }
    return apiRequest(url, options);
}

/**
 * Wrapper POST method
 */
async function apiPost(url, body, errorContext) {
    const options = { method: 'POST', body };
    if (errorContext) {
        options.errorContext = errorContext;
    }
    return apiRequest(url, options);
}

/**
 * Make fetch requests with unified error handling.
 *
 * @param {string} url - The endpoint to hit.
 * @param {string} method - 'GET', 'POST', etc.
 * @param {FormData|object|null} body - Data to send (for POST/PUT).
 * @param {string} errorContext - Custom prefix for error messages.
 */
async function apiRequest(
        url, {
            method = 'GET',
            body = null,
            errorContext = "Request failed"
        } = {})
{
    const options = { method };

    if (body) {
        if (body instanceof FormData) {
            options.body = body;
        } else {
            options.headers = { 'Content-Type': 'application/json' };
            options.body = JSON.stringify(body);
        }
    }

    try {
        const res = await fetch(url, options);
        const contentType = res.headers.get("content-type");
        const got_json = (contentType && contentType.includes("application/json"));

        if (!res.ok) {
            if (got_json) {
                const errorData = await res.json();
                throw new Error(errorData.message || errorContext);
            }
            throw new Error(`${errorContext} (${res.status}: ${res.statusText})`);
        }

        if (res.status === 204 || res.headers.get("content-length") === "0") {
            return true;
        }

        if (got_json) {
            return await res.json();
        }
        return true;
    } catch (err) {
        if (typeof flashMessage === 'function') {
            flashMessage(err.message);
        }
        return null;
    }
}
