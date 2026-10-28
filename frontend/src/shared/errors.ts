import axios from 'axios'

/** Pull a human-readable message out of an Axios/FastAPI error, else use `fallback`. */
export function errorMessage(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
  }
  return fallback
}

/** HTTP status of an Axios error, if any. */
export function errorStatus(err: unknown): number | undefined {
  return axios.isAxiosError(err) ? err.response?.status : undefined
}
