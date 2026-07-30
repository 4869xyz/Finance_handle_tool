export interface CompanyIdentity {
  company_name: string;
  tax_id: string;
}

export interface CompanyProfile {
  version: number;
  identities: CompanyIdentity[];
  updated_at: string;
}

export interface CompanyProfileUpdate {
  identities: CompanyIdentity[];
}
