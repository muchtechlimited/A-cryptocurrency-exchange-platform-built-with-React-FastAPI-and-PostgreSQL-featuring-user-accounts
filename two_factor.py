from pydantic import BaseModel, Field


class TwoFactorSetupResponse(BaseModel):
    """Returned by POST /users/me/2fa/setup. The secret is shown for
    manual entry as a fallback to scanning the QR code; the account
    isn't actually protected by it yet until /enable succeeds."""
    secret: str
    qr_code_data_uri: str
    otpauth_uri: str


class TwoFactorEnableRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class TwoFactorEnableResponse(BaseModel):
    backup_codes: list[str]


class TwoFactorDisableRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10)  # 6-digit TOTP or an 8-char backup code (plus its dash)


class TwoFactorStatusResponse(BaseModel):
    enabled: bool
    remaining_backup_codes: int


class TwoFactorRegenerateBackupCodesRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class TwoFactorRequiredResponse(BaseModel):
    """Returned by POST /auth/login INSTEAD OF a Token when the account
    has 2FA enabled — no access token or session exists yet at this
    point. The frontend shows a code-entry screen and calls
    POST /auth/2fa/verify with temp_token + the code to get the real
    Token this would otherwise have been."""
    requires_2fa: bool = True
    temp_token: str


class TwoFactorVerifyLoginRequest(BaseModel):
    temp_token: str
    code: str = Field(min_length=6, max_length=10)
