export const AUTH_MIN_PASSWORD_LENGTH = 12;

const WEAK = new Set([
  "password", "passw0rd", "password1", "password12", "password123",
  "password1234", "12345678", "123456789", "1234567890", "123456789012",
  "qwertyuiop", "administrator", "letmein", "welcome", "iloveyou",
  "abc123456", "adminadmin", "changeme", "dano123456", "1qaz2wsx3edc",
]);

export class PasswordPolicyError extends Error {
  constructor(message) {
    super(message);
    this.name = "PasswordPolicyError";
  }
}

export function validatePassword(password, { username = "", tenant = "", minLength = AUTH_MIN_PASSWORD_LENGTH } = {}) {
  if (String(password || "").length < minLength) {
    throw new PasswordPolicyError(`密码至少 ${minLength} 位`);
  }
  const folded = String(password).toLowerCase();
  if (WEAK.has(folded)) {
    throw new PasswordPolicyError("密码过于常见,请换一个");
  }
  for (const [label, value] of [["用户名", username], ["租户名", tenant]]) {
    const candidate = String(value || "").trim().toLowerCase();
    if (candidate && folded.includes(candidate)) {
      throw new PasswordPolicyError(`密码不能包含${label}`);
    }
  }
}
