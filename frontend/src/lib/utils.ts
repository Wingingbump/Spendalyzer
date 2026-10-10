import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Merge class names with tailwind-merge
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

/**
 * Format a number as USD currency string
 * e.g. 1234.56 → "$1,234.56"
 */
/**
 * Compact currency for a chart axis, scaled to the value.
 *
 * A fixed `$${v / 1000}k` suits the monthly chart (thousands) but collapses the
 * day-of-week chart (tens of dollars) into a column of "$0k" labels. Pick the
 * unit from the magnitude instead.
 */
export function formatAxisCurrency(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1000) return `$${(value / 1000).toFixed(abs >= 10000 ? 0 : 1)}k`
  if (abs >= 1) return `$${Math.round(value)}`
  return value === 0 ? '$0' : `$${value.toFixed(2)}`
}

export function formatCurrency(amount: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount)
}

/**
 * Format an ISO date string as "Mar 15, 2024"
 */
export function formatDate(date: string): string {
  if (!date) return ''
  // Parse as local date to avoid timezone offset issues (handles both "T" and space separators)
  const parts = date.split(/T| /)[0].split('-')
  const d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]))
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

/**
 * Format "YYYY-MM" string as "Mar 2024"
 */
export function formatMonth(month: string): string {
  if (!month) return ''
  const [year, mon] = month.split('-')
  const d = new Date(Number(year), Number(mon) - 1, 1)
  return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
}

/**
 * Truncate a string to maxLen chars, adding ellipsis if needed
 */
export function truncate(str: string, maxLen: number): string {
  if (!str) return ''
  if (str.length <= maxLen) return str
  return str.slice(0, maxLen) + '…'
}

/**
 * Get chart color by index from the palette
 */
const DARK_CHART_COLORS = ['#1a56db', '#b5c4ff', '#7aaed4', '#5abf8a', '#e8c17a', '#c47adb']
const LIGHT_CHART_COLORS = ['#1a56db', '#7aaed4', '#5a9fd4', '#5abf8a', '#e8c17a', '#c47adb']

export function getChartColor(index: number, theme: 'dark' | 'light' = 'dark'): string {
  const palette = theme === 'dark' ? DARK_CHART_COLORS : LIGHT_CHART_COLORS
  return palette[index % palette.length]
}

export const CHART_COLORS_DARK = DARK_CHART_COLORS
export const CHART_COLORS_LIGHT = LIGHT_CHART_COLORS

/**
 * Consistent per-category colors for UI accents (dots, badges, etc.)
 */
const CATEGORY_COLOR_MAP: Record<string, string> = {
  'Food & Drink':       '#f97316',
  'Groceries':          '#22c55e',
  'Transport':          '#3b82f6',
  'Shopping':           '#a855f7',
  'Subscriptions':      '#06b6d4',
  'Health & Fitness':   '#ec4899',
  'Utilities':          '#eab308',
  'Travel':             '#0ea5e9',
  'Entertainment':      '#d946ef',
  'Personal Care':      '#f43f5e',
  'Home':               '#14b8a6',
  'Education':          '#6366f1',
  'Business Services':  '#7c3aed',
  'Income':             '#10b981',
  'Transfer':           '#94a3b8',
  'Other':              '#6b7280',
}

const FALLBACK_COLORS = [
  '#f97316', '#22c55e', '#3b82f6', '#a855f7', '#06b6d4',
  '#ec4899', '#eab308', '#0ea5e9', '#d946ef', '#f43f5e',
]

const _dynamicCache: Record<string, string> = {}
let _dynamicIndex = 0

export function getCategoryColor(category: string): string {
  if (!category) return '#6b7280'
  if (CATEGORY_COLOR_MAP[category]) return CATEGORY_COLOR_MAP[category]
  if (_dynamicCache[category]) return _dynamicCache[category]
  const color = FALLBACK_COLORS[_dynamicIndex % FALLBACK_COLORS.length]
  _dynamicCache[category] = color
  _dynamicIndex++
  return color
}
