export interface SsidSuggestionOption {
  name: string;
  universe: string;
}

export interface SsidCategoryOptions {
  label: string;
  description: string;
  icon: string | null;
  options: SsidSuggestionOption[];
}

export interface UniverseCombo {
  id: string;
  label: string;
  description: string;
  ssids: Record<string, string>; // category → SSID name
}

export interface SsidSuggestionsLibrary {
  categories: Record<string, SsidCategoryOptions>;
  universe_combos: UniverseCombo[];
}
