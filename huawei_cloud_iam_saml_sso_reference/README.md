# Huawei Cloud IAM SAML SSO Configuration Reference

This document provides a comprehensive summary of Huawei Cloud IAM SAML-based Single Sign-On (SSO) configuration, covering both Virtual User SSO and IAM User SSO modes.

---

## Table of Contents

1. [SSO Mode Comparison: Virtual User SSO vs IAM User SSO](#1-sso-mode-comparison)
2. [Step 1: Creating an Identity Provider](#2-creating-an-identity-provider)
3. [Step 2: Configuring SAML Metadata Exchange](#3-configuring-saml-metadata-exchange)
4. [Step 3: Configuring Identity Conversion Rules (Virtual User SSO)](#4-configuring-identity-conversion-rules)
5. [Step 3: Configuring External Identity ID (IAM User SSO)](#5-configuring-external-identity-id)
6. [Step 4: Login Verification](#6-login-verification)
7. [Step 5: Configuring Enterprise Management System Login Portal (Optional)](#7-configuring-enterprise-login-portal)
8. [Federated Authentication Interaction Flow](#8-federated-authentication-interaction-flow)
9. [Common Error Scenarios and Important Notes](#9-common-errors-and-important-notes)

---

## 1. SSO Mode Comparison

Huawei Cloud supports two identity provider types for SAML-based SSO:

### Virtual User SSO

- When IdP users log in to Huawei Cloud, the system **automatically creates virtual user information** and grants permissions based on **identity conversion rules**.
- **Applicable scenarios:**
  - You do NOT want to create and manage IAM users on the cloud platform (avoiding user synchronization overhead).
  - You want to distinguish cloud permissions based on user groups or special attributes in the local enterprise IdP.
  - Multiple branch offices have multiple enterprise IdPs that all need to access the same Huawei Cloud account (supports multiple IdPs per account).
- **Key constraint:** A single account can have **multiple** Virtual User SSO identity providers.

### IAM User SSO

- When IdP users log in, the system **automatically matches the corresponding IAM sub-user** via External Identity ID binding, inheriting that IAM user's group permissions.
- **Applicable scenarios:**
  - Some cloud products do not yet support Virtual User SSO access (e.g., DevCloud/software development platform).
  - You want to simplify IdP configuration and prefer managing users directly in IAM.
- **Key constraint:** A single account can have **only one** IAM User SSO identity provider.
- **Important:** An account can have ONLY ONE type of identity provider (either Virtual User SSO or IAM User SSO, not both).

### Key Differences

| Aspect | Virtual User SSO | IAM User SSO |
|--------|-----------------|--------------|
| Identity conversion method | Identity conversion rules (JSON) | External Identity ID matching (`IAM_SAML_Attributes_xUserId`) |
| User visibility in IAM | Not visible in IAM user list (temporary virtual users) | IAM sub-users exist in the user list |
| Permission assignment | Determined by identity conversion rules mapping to IAM user groups | Inherits IAM sub-user's group permissions directly |
| Max IdPs per account | Multiple | Only one |
| IdP-side assertion requirement | Standard SAML attributes | Must include `IAM_SAML_Attributes_xUserId` attribute |
| IAM-side requirement | Create user groups and assign permissions | Create IAM users with External Identity IDs and assign to groups |

---

## 2. Creating an Identity Provider

### Prerequisites

- Enterprise administrator has access to the enterprise IdP and its help documentation.
- A valid Huawei Cloud account is registered.
- For Virtual User SSO: IAM user groups have been created and authorized in advance.
- For IAM User SSO: IAM users with External Identity IDs have been created.

### Procedure

1. Log in to Huawei Cloud and navigate to the **IAM console**.
2. In the left navigation pane, click **"Identity Providers"** (身份提供商).
3. Click **"Create Identity Provider"** (创建身份提供商) in the upper right corner.
4. Fill in the parameters:

| Parameter | Description |
|-----------|-------------|
| **Name** | Unique name for the identity provider. Must be globally unique within the account. Recommended to use the domain name as a unique identifier. |
| **Protocol** | Select **SAML**. (OIDC is also supported but is a separate configuration path.) |
| **Type** | For Virtual User SSO: select **"Virtual User SSO"** (虚拟用户SSO). For IAM User SSO: select **"IAM User SSO"** (IAM用户SSO). |
| **Status** | Default is **"Enabled"** (启用). |
| **Description** | Optional description for the identity provider. |

5. Click **"OK"** to create the identity provider.

### Important Notes

- An account can have only **one type** of identity provider. You cannot mix Virtual User SSO and IAM User SSO in the same account.
- For IAM User SSO type, only **one** identity provider can be created per account.
- For Virtual User SSO type, **multiple** identity providers can be created per account.

---

## 3. Configuring SAML Metadata Exchange

After creating the identity provider, you must configure metadata to establish mutual trust between the enterprise IdP and Huawei Cloud. This is a **two-way exchange**:

### 3a. Upload Huawei Cloud Metadata to Enterprise IdP

- On the IAM console Identity Provider detail page, download or copy the Huawei Cloud metadata (SP metadata).
- Upload this metadata file to the enterprise IdP system (e.g., ADFS, Shibboleth, Keycloak).
- The specific steps vary by IdP -- consult your IdP's documentation.

### 3b. Upload Enterprise IdP Metadata to Huawei Cloud

Two methods are supported:

#### Method 1: Upload File (Recommended)

1. In the identity provider list, click **"Modify"** (修改) in the Operations column.
2. Under "Upload File", click **"Add File"** (添加文件) and select the enterprise IdP's metadata XML file.
3. Click **"Upload File"** (上传文件).
4. The system extracts metadata from the file. Review the extracted information and click **"OK"**.
5. If the system detects multiple identity providers in the file, select the correct one from the **Entity ID** dropdown.
6. Click **"OK"** to save.

#### Method 2: Manual Edit (For files over 500KB)

1. In the identity provider list, click **"Modify"**.
2. Click **"Manual Edit"** (手动编辑).
3. Fill in the following parameters extracted from the IdP metadata XML:

| Parameter | Required | Description |
|-----------|----------|-------------|
| **Entity ID** | Yes | The `entityID` value from the IdP metadata. Uniquely identifies the enterprise IdP. |
| **Supported Protocol** | Yes (auto) | SAML protocol. Automatically generated -- no manual selection needed. |
| **NameIdFormat** | No | The `NameIdFormat` value from the IdP metadata. Supports multiple values; Huawei Cloud uses the first one by default. |
| **Signing Certificate** | Yes | The `<X509Certificate>` value from the IdP metadata. Used to verify assertion signatures. **Must be >= 2048 bits.** Supports multiple; Huawei Cloud uses the first by default. |
| **SingleSignOnService** | Yes | The SSO endpoint URL from the IdP metadata. Must support HTTP Redirect or HTTP POST binding. Supports multiple; Huawei Cloud uses the first by default. |
| **SingleLogoutService** | No | The SLO endpoint URL from the IdP metadata. Must support HTTP Redirect or HTTP POST binding. Supports multiple; Huawei Cloud uses the first by default. |

4. Click **"OK"** to save.

### Important Notes on Metadata

- If the metadata file exceeds **500KB**, you must use the "Manual Edit" method.
- If the IdP metadata is updated (e.g., certificate rotation), you **must re-upload or re-edit** the metadata on Huawei Cloud, otherwise federated users will be unable to log in.
- If the system warns about empty Entity ID or expired signing certificates, verify the metadata file's correctness and re-upload.

---

## 4. Configuring Identity Conversion Rules (Virtual User SSO)

Identity conversion rules determine the federated user's identity and permissions when they log in to Huawei Cloud. Without configuration, the default federated username is **"FederationUser"** with no permissions beyond basic cloud access.

### Prerequisites

- An IAM user group has been created and authorized with the desired permissions.
- An identity provider has been created and metadata configured.

### What You Can Configure

- **Username:** The display name for the federated user in Huawei Cloud.
- **User Group:** The IAM user group the federated user belongs to (inheriting its permissions).
- **Rule Conditions:** Conditions that must be met for the rule to take effect.

### Method 1: Create Rule (Visual Builder)

1. In the IAM console, navigate to **"Identity Providers"**.
2. Select the identity provider and click **"Modify"**.
3. In the **"Identity Conversion Rules"** area, click **"Create Rule"**.
4. Fill in the rule parameters:

| Parameter | Description |
|-----------|-------------|
| **Username** | The federated user's display name in Huawei Cloud. Recommendation: use format `FederationUser-IdP_XXX` where IdP is the provider name (e.g., ADFS, Keycloak) and XXX is a custom identifier. Supports placeholders `{0..n}` where `{0}` is the first attribute from the remote user info. Can also be any string not containing `<`, `>`, `{`, `}`. **Must be unique within the same identity provider.** Duplicate names map to the same IAM user. |
| **User Group** | Select an existing IAM user group. The federated user inherits this group's permissions. |
| **Rule Conditions** | Up to 10 conditions per rule. Each condition has: **Attribute** (an IdP SAML assertion attribute), **Condition Type** (`empty`, `any_one_of`, `not_any_of`), **Value** (the attribute value to match). ALL conditions must be satisfied for the rule to take effect. |

5. Click **"OK"** to save the rule.
6. Click **"OK"** on the Modify Identity Provider page to apply changes.

#### Example Rule

- Username: `FederationUser-IdP_admin`
- User Group: `admin`
- Condition: Attribute = `_NAMEID_`, Condition = `any_one_of`, Value = `000000001`

This means only the user with ID `000000001` will be mapped to the `FederationUser-IdP_admin` IAM username with `admin` group privileges.

### Method 2: Edit Rule (Direct JSON Editing)

1. In the IAM console, navigate to **"Identity Providers"**.
2. Select the identity provider and click **"Modify"**.
3. In the **"Identity Conversion Rules"** area, click **"Edit Rule"**.
4. Enter the identity conversion rules in JSON format.
5. Click **"Validate Rule"** (校验规则) to verify syntax.
6. If validation passes ("Rule is correct"), click **"OK"** and then **"OK"** again to apply.
7. If validation fails ("JSON file format is incomplete"), fix the JSON or click "Cancel" to discard changes.

### Important Notes on Identity Conversion Rules

- Modified rules do **NOT** take effect for already-logged-in federated users immediately. Users must **log out and log in again** for new rules to apply.
- If you need to change a federated user's permissions, modify the IAM user group's permissions. After modifying group permissions, **restart the enterprise IdP** for changes to take effect.
- A single identity provider can have **multiple rules** that work together.
- If **ALL rules fail to match** a federated user, that user is **denied access** to Huawei Cloud.

---

## 5. Configuring External Identity ID (IAM User SSO)

For IAM User SSO, the mapping between enterprise IdP users and Huawei Cloud IAM users is done through **External Identity IDs**.

### Key Concept

- The enterprise IdP must send a SAML assertion attribute named **`IAM_SAML_Attributes_xUserId`**.
- The value of this attribute must match the **External Identity ID** configured on the corresponding IAM user in Huawei Cloud.
- When an IdP user logs in, Huawei Cloud looks up the IAM user with a matching External Identity ID and logs in as that IAM user.

### Procedure: Create IAM User with External Identity ID

1. Log in to the IAM console as an administrator.
2. In the left navigation pane, select **"Users"** (用户).
3. Click **"Create User"** (创建用户).
4. In the user creation form, fill in the **"External Identity ID"** (外部身份ID) field under user information. This value must match the `IAM_SAML_Attributes_xUserId` value sent by the enterprise IdP.

### Procedure: Modify Existing IAM User's External Identity ID

1. In the IAM user list, click the username or click **"Security Settings"** (安全设置) on the right.
2. View or modify the **External Identity ID** field.

### Important Notes

- The External Identity ID must be **unique** for each IAM user.
- Multiple IdP users can share the same `IAM_SAML_Attributes_xUserId` value, which maps them all to the same IAM user.
- You must configure both sides: the IdP assertion must include `IAM_SAML_Attributes_xUserId`, AND the IAM user must have the matching External Identity ID.

---

## 6. Login Verification

After completing the identity provider configuration, verify the SSO login flow:

### Procedure

1. In the IAM console, navigate to **"Identity Providers"**.
2. Click **"View"** (查看) on the target identity provider.
3. Copy the **Login Link** (登录链接) displayed on the identity provider detail page.
4. Open the login link in a browser.
5. The browser redirects to the enterprise IdP's login page.
6. Enter enterprise IdP credentials.
7. Upon successful authentication, the IdP sends a SAML Response to Huawei Cloud.
8. Huawei Cloud validates the assertion, maps the user identity (via identity conversion rules for Virtual User SSO, or External Identity ID for IAM User SSO), and grants a token.
9. The user is redirected to the Huawei Cloud console with the appropriate permissions.

### Expected Results

- For Virtual User SSO: The user is logged in with the virtual username specified in the identity conversion rules and has the permissions of the mapped user group.
- For IAM User SSO: The user is logged in as the IAM user whose External Identity ID matches the `IAM_SAML_Attributes_xUserId` in the SAML assertion, inheriting that IAM user's group permissions.

---

## 7. Configuring Enterprise Login Portal (Optional)

After successful login verification, you can optionally configure the Huawei Cloud login link in the enterprise management system for a seamless user experience.

### Prerequisites

- Identity provider has been created.
- Enterprise management system has a login portal where the Huawei Cloud link can be embedded.

### Procedure

1. In the IAM console, navigate to **"Identity Providers"**.
2. Click **"View"** on the target identity provider.
3. Copy the **Login Link**.
4. Add the following HTML to the enterprise management system page:

```html
<a href="<Login Link>"> Huawei Cloud Login </a>
```

5. Enterprise users can now click this link after logging in to the enterprise system to access Huawei Cloud directly.

### Alternative: Enterprise Federated User Login Portal

Huawei Cloud also provides an Enterprise Federated User Login portal. If the enterprise management system login entry is not configured, federated users can use this portal to log in to Huawei Cloud directly.

---

## 8. Federated Authentication Interaction Flow

The complete SAML federated authentication flow between the enterprise IdP and Huawei Cloud:

1. **User initiates SSO:** The user opens the identity provider's login link in a browser. The browser sends a single sign-on request to Huawei Cloud.

2. **Huawei Cloud builds SAML Request:** Huawei Cloud looks up the identity provider's metadata, constructs a SAML authentication request, and sends it to the browser.

3. **Browser forwards to IdP:** The browser receives the request and forwards the SAML Request to the enterprise IdP.

4. **User authenticates at IdP:** The user enters credentials on the IdP's login page. The IdP validates the credentials, constructs a SAML assertion containing user information, and sends a SAML Response to the browser.

5. **Browser forwards SAML Response:** The browser forwards the SAML Response to Huawei Cloud.

6. **Huawei Cloud processes assertion:** Huawei Cloud extracts the assertion from the SAML Response and:
   - For Virtual User SSO: Maps the user to IAM user groups based on the configured identity conversion rules.
   - For IAM User SSO: Looks up the IAM user by matching `IAM_SAML_Attributes_xUserId` with the External Identity ID.

7. **Token issued and login complete:** Huawei Cloud issues a token and the user gains access to the cloud console.

### Debugging Tip

Install the **"SAML Message Decoder"** Chrome extension to inspect SAML request and assertion messages during the authentication flow for troubleshooting.

---

## 9. Common Errors and Important Notes

### Common Error Scenarios

| Error Scenario | Root Cause | Resolution |
|---------------|-----------|------------|
| **Login fails: assertion missing signature** | The SAML assertion from the IdP does not include a digital signature. | Ensure the IdP is configured to sign SAML assertions. Huawei Cloud **requires** signed assertions. |
| **Federated user gets "FederationUser" with no permissions** | No identity conversion rules have been configured (Virtual User SSO default behavior). | Configure identity conversion rules to map users to IAM user groups with appropriate permissions. |
| **"Entity ID is empty" warning during metadata upload** | The IdP metadata file does not contain a valid Entity ID. | Verify the metadata file is correct and re-upload, or use manual edit to fill in the Entity ID. |
| **"Signing certificate expired" warning** | The IdP's signing certificate in the metadata has expired. | Renew the IdP's signing certificate, regenerate the metadata file, and re-upload to Huawei Cloud. |
| **Federated user denied access** | None of the identity conversion rules match the user's SAML attributes. | Ensure the rule conditions match the attributes sent by the IdP in the SAML assertion. Check attribute names and values. |
| **Modified rules not taking effect** | Identity conversion rule changes do not apply to already-logged-in sessions. | The federated user must log out and log in again for new rules to take effect. |
| **Metadata changes break login** | IdP metadata was updated (e.g., certificate rotation) but not updated on Huawei Cloud. | Re-upload or re-edit the IdP metadata on Huawei Cloud whenever the IdP metadata changes. |
| **IAM User SSO: user not found** | The `IAM_SAML_Attributes_xUserId` value in the SAML assertion does not match any IAM user's External Identity ID. | Ensure the IdP sends the correct `IAM_SAML_Attributes_xUserId` attribute and that the IAM user has the matching External Identity ID configured. |
| **Cannot create identity provider: type conflict** | An account can only have one type of identity provider. | Delete the existing identity provider if you need to switch between Virtual User SSO and IAM User SSO types. |
| **Cannot create second IAM User SSO provider** | Only one IAM User SSO identity provider is allowed per account. | Modify the existing IAM User SSO provider instead of creating a new one. |
| **Metadata file upload fails (file too large)** | Metadata file exceeds 500KB. | Use the "Manual Edit" method to enter metadata parameters individually. |

### Critical Requirements Checklist

- [ ] Enterprise IdP must support **SAML 2.0 protocol**.
- [ ] SAML assertions **MUST include a digital signature** (unsigned assertions will cause login failure).
- [ ] Signing certificate should be **>= 2048 bits** for security.
- [ ] For IAM User SSO: IdP must send `IAM_SAML_Attributes_xUserId` attribute in the SAML assertion.
- [ ] For IAM User SSO: IAM users must have External Identity IDs that match the IdP's `IAM_SAML_Attributes_xUserId` values.
- [ ] For Virtual User SSO: Identity conversion rules must be configured to grant meaningful permissions.
- [ ] User group names in identity conversion rules must match **existing IAM user groups**.
- [ ] Metadata must be re-uploaded whenever the IdP updates its configuration.

### Best Practices

- Use naming convention `FederationUser-IdP_XXX` for federated usernames to distinguish users from different IdPs.
- Ensure federated usernames are **unique** within the same identity provider to avoid unintended user mapping.
- Use the Chrome "SAML Message Decoder" extension for debugging SAML assertion contents.
- Keep the IdP metadata synchronized with Huawei Cloud -- automate re-upload if possible.
- Plan the identity conversion rules carefully; if no rules match a user, they are completely denied access.
- For Virtual User SSO with multiple branches/IdPs, leverage the ability to create multiple identity providers.

---

## Source Documentation URLs

| Document | URL |
|----------|-----|
| Virtual User SSO Overview | https://support.huaweicloud.com/usermanual-iam/iam_08_0002.html |
| Creating Identity Provider (Virtual User SSO) | https://support.huaweicloud.com/usermanual-iam/iam_08_0003.html |
| Configuring Identity Conversion Rules | https://support.huaweicloud.com/usermanual-iam/iam_08_0004.html |
| Configuring Enterprise Login Portal | https://support.huaweicloud.com/usermanual-iam/iam_08_0005.html |
| Virtual User SSO vs IAM User SSO Comparison | https://support.huaweicloud.com/usermanual-iam/iam_08_0251.html |
| IAM User SSO Overview | https://support.huaweicloud.com/usermanual-iam/iam_08_0254.html |
| Creating Identity Provider (IAM User SSO) | https://support.huaweicloud.com/usermanual-iam/iam_08_0255.html |
| Configuring External Identity ID | https://support.huaweicloud.com/usermanual-iam/iam_08_0257.html |
| Login Verification (IAM User SSO) | https://support.huaweicloud.com/usermanual-iam/iam_08_0258.html |
| IAM User Guide PDF (includes Keycloak section 9.5.2) | https://support.huaweicloud.com/usermanual-iam/iam-usermanual-hws-zh-pdf.pdf |

### Note on Original Best Practice URLs

The originally requested best practice URLs for ADFS and Keycloak SAML setup returned 404 errors:
- `bestpractice_0003.html` (ADFS SAML setup) -- no longer available at this URL
- `bestpractice_0026.html` (Keycloak SAML setup) -- no longer available at this URL

Alternative resources found:
- ADFS SAML 2.0 example (Workspace context): https://support.huaweicloud.com/bestpractice-workspace/workspace_08_0018.html
- Keycloak federation configuration: Refer to the IAM User Guide PDF section 9.5.2, and the community forum post at https://bbs.huaweicloud.com/forum/thread-0213195720814779022-1-1.html
- OneAccess SAML-IAM User SSO: https://support.huaweicloud.com/intl/zh-cn/bestpractice-oneaccess/oneaccess_05_0146.html
- OneAccess SAML-Virtual User SSO: https://support.huaweicloud.com/bestpractice-oneaccess/oneaccess_05_0029.html
