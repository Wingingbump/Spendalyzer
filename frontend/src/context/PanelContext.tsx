import { createContext, useContext } from 'react'

export interface TabFocusRequest {
  tab: string
  nonce: number
}

interface PanelContextType {
  panelOpen: boolean
  // Open the side panel (if collapsed) and switch it to the given tab.
  focusTab: (tab: string) => void
  // Latest focus request; RightPanel watches this to switch tabs.
  requestedTab: TabFocusRequest | null
}

export const PanelContext = createContext<PanelContextType>({
  panelOpen: true,
  focusTab: () => {},
  requestedTab: null,
})
export const usePanel = () => useContext(PanelContext)
