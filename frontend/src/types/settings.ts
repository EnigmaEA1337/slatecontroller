export interface SSHKeypairStatus {
  generated: boolean;
  public_openssh: string | null;
  fingerprint_sha256: string | null;
  created_at: string | null;
  deployed_to_slate: boolean;
  deployed_at: string | null;
  auth_mode: "key" | "password";
}

export interface DeployRequest {
  disable_password_auth: boolean;
}

export interface DeployResponse {
  deployed: boolean;
  password_auth_disabled: boolean;
  note: string;
}
