import type { HealthResponse } from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL as string

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/health`)
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`)
  }
  return (await response.json()) as HealthResponse
}
