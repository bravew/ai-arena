import { useContext } from 'react';
import { createContext } from 'react';
import type { Bundle, SchemaIssue, Validated } from '../lib/schema';

export interface BundleState {
  bundle: Bundle | undefined;
  validation: Validated<Bundle> | undefined;
  loadBundle: (input: unknown) => void;
  setLoadError: (message: string) => void;
}

export const BundleContext = createContext<BundleState | undefined>(undefined);

export function useBundleState(): BundleState {
  const state = useContext(BundleContext);
  if (!state) throw new Error('useBundleState must be used inside BundleProvider');
  return state;
}

export type { SchemaIssue };
