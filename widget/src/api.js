/**
 * Calls to the widget API (/api/v1/widget, ADR 0035). The API lives on the origin the script
 * was loaded from; no cookies are sent (credentials: "omit"), so there is no session to ride.
 */

/** A refusal of the API: `code` is the error code, `field` the contact input it concerns. */
export class ApiError extends Error {
  /**
   * @param {string} code
   * @param {number} status
   * @param {string | null} field
   */
  constructor(code, status, field = null) {
    super(code);
    this.code = code;
    this.status = status;
    this.field = field;
  }
}

/**
 * Return the API client of one group.
 * @param {string} origin Origin of the script, e.g. "https://afspraak.gemeente.nl".
 * @param {string} organisation Organisation slug.
 * @param {string} group Group slug.
 * @param {string} lang Locale for texts and labels.
 */
export function createApi(origin, organisation, group, lang) {
  const base = `${origin}/api/v1/widget/${encodeURIComponent(organisation)}/${encodeURIComponent(group)}`;

  async function call(path, params = {}, init = {}) {
    const url = new URL(`${base}${path}`);
    url.searchParams.set('lang', lang);
    for (const [name, value] of Object.entries(params)) {
      if (value) url.searchParams.set(name, value);
    }
    let response;
    try {
      response = await fetch(url, { credentials: 'omit', mode: 'cors', ...init });
    } catch {
      throw new ApiError('network', 0);
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(body.error ?? 'invalid_request', response.status, body.field ?? null);
    return body;
  }

  return {
    texts: async () => {
      const url = new URL(`${origin}/api/v1/widget/texts`);
      url.searchParams.set('lang', lang);
      const response = await fetch(url, { credentials: 'omit', mode: 'cors' }).catch(() => null);
      if (!response?.ok) throw new ApiError('network', response?.status ?? 0);
      return response.json();
    },
    group: () => call('/'),
    availability: (month, day) => call('/availability', { month, day }),
    book: (details) =>
      call('/bookings', {}, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(details),
      }),
  };
}
