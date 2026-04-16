# Isomer — Agents Document

**Version:** Alpha
**Last Updated:** April 2026

---

## 1. Purpose

This document describes the human roles (agents) that interact with Isomer, their responsibilities within the compliance tracking workflow, and how the system's permission model maps to real-world audit activities. It is intended for project leads deploying Isomer and for team members being onboarded onto a compliance engagement.

---

## 2. Role Definitions

Isomer defines three roles with a strict hierarchy. Each higher role inherits all capabilities of the roles below it.

### 2.1 Reporter (Read-Only)

**System role:** `reporter`
**Permission level:** 1 (lowest)

**Who this is for:** Executives, department managers, project stakeholders, and anyone who needs visibility into audit progress without modifying data. This role is ideal for people who need to check status, review reports, or understand what evidence is needed from their teams — without the risk of accidentally changing control data.

**What they can do:**
- View the dashboard and all company engagements
- Browse controls by section, framework, status, or tag
- Search across all controls and notes
- Open individual controls and read all detail panels (description, explanation, real-world application, affected teams, stakeholders)
- View uploaded evidence files in the browser
- View and print the in-browser audit report
- Download the report ZIP and company export ZIP
- See all company contacts

**What they cannot do:**
- Change a control's status, notes, assignment, or tags
- Upload or delete evidence
- Add or remove contacts
- Create, delete, or import companies
- Manage users or access settings

**Typical users:**
- CISO or VP of Security (when reviewing, not editing)
- Department heads checking their team's control obligations
- Board members or executives reviewing audit readiness
- External consultants given read access for assessment

---

### 2.2 Auditor (Write Access)

**System role:** `auditor`
**Permission level:** 2

**Who this is for:** The hands-on compliance team — the people actively working through controls, gathering evidence, writing implementation notes, and tracking progress. This is the primary working role for day-to-day audit activity.

**What they can do (in addition to all Reporter capabilities):**
- Update control status (new → in progress → stalled → closed)
- Write and edit implementation notes on any control
- Assign controls to individuals (name and email)
- Add and remove tags for categorization and filtering
- Mark prior evidence as valid (for renewal engagements)
- Upload evidence files (screenshots, logs, documents, policies) to any control
- Delete evidence files that were uploaded in error
- Add and remove company contacts

**What they cannot do:**
- Create or delete companies
- Import company data from ZIP archives
- Manage users (create, edit, delete accounts)
- Access the settings page

**Typical users:**
- GRC (Governance, Risk, and Compliance) analysts
- Internal auditors actively working an engagement
- IT security team members gathering technical evidence
- Compliance coordinators managing the evidence collection process
- External auditors given write access to document findings

---

### 2.3 Admin (Full Access)

**System role:** `admin`
**Permission level:** 3 (highest)

**Who this is for:** The Isomer system owner — typically the compliance lead, ISMS manager, or IT administrator responsible for setting up and maintaining the platform. This role controls who has access and manages the lifecycle of company engagements.

**What they can do (in addition to all Auditor capabilities):**
- Create new company engagements (selecting frameworks and engagement type)
- Delete companies and all associated data
- Import company data from previously exported ZIP files
- Access the settings page (port 27000)
- Create new user accounts with any role
- Edit existing user accounts (change display name, email, role, password)
- Delete user accounts
- View system information (version, ports, database details)

**What they cannot do:**
- There are no restrictions on the admin role within the application
- The only protection is that the last remaining admin account cannot be deleted through the UI

**Typical users:**
- ISMS Manager or Compliance Lead
- IT Administrator responsible for the tool
- Project lead for the certification engagement

---

## 3. Workflow by Role

### 3.1 Engagement Setup (Admin)

The admin initializes a new compliance engagement:

1. **Create company** — Enter company name, description, select frameworks (ISO 27001, SOC 2, or both), and choose engagement type (first-time or renewal).
2. **Create user accounts** — Set up accounts for team members with appropriate roles. Share credentials securely.
3. **Add company contacts** — Enter the key contacts at the client organization (or internally) with name, email, phone, and department.

On company creation, Isomer automatically populates all controls for the selected frameworks. For ISO 27001, this means 93 Annex A controls across four themes. For SOC 2, 44 Trust Services Criteria across nine common categories plus optional categories. Both frameworks together yield 137 controls, each pre-loaded with descriptions, explanations, challenge ratings, and stakeholder information.

### 3.2 Evidence Gathering (Auditor)

Auditors work through controls systematically:

1. **Assign controls** — Assign each control to the person responsible for producing evidence. The assigned name and email are visible on the control and in reports.
2. **Tag controls** — Apply tags for additional categorization (e.g., "critical", "network", "hr-process") to enable cross-cutting views beyond the section structure.
3. **Update status** — Move controls through the lifecycle:
   - `new` → Control has been identified but no work has started
   - `in_progress` → Evidence gathering or implementation is underway
   - `stalled` → Work is blocked (document the reason in notes)
   - `closed` → Evidence is complete and the control is satisfied
4. **Write notes** — Document implementation decisions, evidence requirements, action items, blockers, or auditor observations.
5. **Upload evidence** — Attach screenshots, policy documents, configuration exports, log samples, or any supporting file. Multiple files can be uploaded at once. Evidence is viewable in the browser for images, PDFs, and text files.
6. **Manage contacts** — Add or update contacts as the engagement progresses and new stakeholders are identified.

For **renewal engagements**, auditors can flag individual controls where prior evidence remains valid, avoiding unnecessary re-collection.

### 3.3 Progress Monitoring (Reporter)

Reporters track engagement health:

1. **Dashboard review** — See all companies with completion percentages and status breakdowns (new, in progress, stalled, closed).
2. **Filtered views** — Drill into specific sections, frameworks, or statuses to understand where bottlenecks exist.
3. **Search** — Find specific controls by ID, title keyword, or tag.
4. **Report generation** — View the in-browser report for a quick summary or download the ZIP report for a complete audit package including organized evidence.

### 3.4 Audit Delivery (All Roles)

When the engagement reaches completion:

1. **In-browser report** — View the full audit report with all controls, statuses, notes, and evidence thumbnails. Print to PDF directly from the browser using the print button (uses print-optimized CSS).
2. **ZIP report** — Download a ZIP archive containing a Markdown audit report and all evidence files organized into folders by framework, section, and control ID.
3. **Company export** — Export the entire company dataset as a ZIP for archiving, migration to another Isomer instance, or as a baseline for the next renewal cycle.

---

## 4. Role Assignment Guidance

| Scenario | Recommended Role |
|----------|-----------------|
| Running the day-to-day audit | Auditor |
| Occasional check-in from management | Reporter |
| External auditor reviewing evidence | Reporter |
| External consultant helping gather evidence | Auditor |
| IT admin maintaining the Isomer deployment | Admin |
| Compliance lead managing the engagement | Admin |
| Department head uploading evidence for their area | Auditor |
| Board member reviewing readiness | Reporter |
| Contractor building policies | Auditor |
| Multiple teams sharing a single Isomer instance | One Admin, several Auditors, Reporters as needed |

---

## 5. Multi-Agent Coordination

### 5.1 Assignment Model

Controls are assigned to individuals by name and email, but Isomer does not send notifications. Coordination happens outside the tool — the assignment field serves as a record of responsibility. Teams typically supplement Isomer with:

- A kickoff meeting to walk through control assignments
- Weekly standups reviewing stalled controls and upcoming deadlines
- The filtered view (status = stalled) to identify blockers during team meetings

### 5.2 Tagging for Cross-Functional Work

Tags enable views that cut across the section-based structure. Common tagging patterns:

- **By team:** `it-team`, `hr-team`, `legal-team`, `facilities-team`
- **By priority:** `critical`, `quick-win`, `complex`
- **By evidence type:** `policy-needed`, `technical-config`, `interview-required`
- **By audit phase:** `phase-1`, `phase-2`, `remediation`
- **By risk area:** `access-control`, `encryption`, `vendor-management`

Any user with Auditor or Admin role can add tags. Reporters can filter by tag to see just the controls relevant to their area.

### 5.3 Contact Management

The contacts section on each company serves as a shared directory for the engagement. Typical entries include:

- Client-side stakeholders (IT Director, HR Director, Facilities Manager)
- Internal team members with their departments
- External auditors or consultants
- Vendor contacts relevant to third-party controls

Contacts are per-company, not per-control. The control assignment field links specific controls to responsible individuals, while the contacts section provides the broader engagement directory.

---

## 6. Lifecycle of a Control

```
                    ┌──────────┐
                    │   NEW    │  ← Created when company is set up
                    └────┬─────┘
                         │
                    Auditor begins work
                         │
                    ┌────▼─────────┐
                    │ IN PROGRESS  │  ← Evidence being gathered
                    └────┬─────────┘
                         │
              ┌──────────┼──────────┐
              │                     │
         Blocked                 Evidence
              │                  complete
         ┌────▼────┐                │
         │ STALLED │           ┌────▼────┐
         └────┬────┘           │ CLOSED  │  ← Control satisfied
              │                └─────────┘
         Blocker resolved
              │
              └────► back to IN PROGRESS
```

Status transitions are unrestricted — an auditor can move a control to any status at any time. There is no enforced workflow. This flexibility is intentional: real audit engagements are messy, and controls sometimes need to be reopened, re-stalled, or jumped ahead based on auditor judgment.

---

## 7. Renewal Engagements

When a company is set up as a renewal engagement, each control gains an additional checkbox: "Prior evidence is still valid." This flag is significant because many controls in a recertification audit can retain evidence from the prior period if nothing material has changed.

The renewal workflow:

1. **Admin** creates the company as a renewal (or imports a prior engagement's export and changes the type)
2. **Auditor** reviews each control and determines whether prior evidence is sufficient
3. Controls where prior evidence is valid are flagged — the auditor can focus their effort on controls requiring fresh evidence
4. The report and dashboard reflect which controls have been actively re-evidenced vs. carried forward

---

## 8. Default Credentials

Isomer ships with a single default account:

| Username | Password | Role |
|----------|----------|------|
| `admin` | `admin` | Admin |

**The first action after deployment should be to change the admin password and create individual accounts for each team member.** Shared accounts undermine accountability — every user should have their own login so that evidence uploads and note edits are attributed to the correct person.

User accounts are managed through the settings page at port 27000, accessible only to users with the Admin role.
