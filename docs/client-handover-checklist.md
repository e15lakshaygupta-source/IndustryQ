# Client handover and acceptance checklist

Complete one copy for each client environment. Attach the approved configuration record separately; do not record passwords, secret keys, API keys, or recovery codes in this checklist.

## Client and environment

- Client legal name: ______________________________
- Environment and URL: ___________________________
- Handover date: __________________________________
- Client owner: ____________________________________
- Delivery lead: ___________________________________
- Support contact and route: _______________________

## Organization and accounts

- [ ] Company legal name, contact details, and registration details reviewed with the client.
- [ ] Each factory is attached to the correct company; factory codes, state/GST details, and active status verified.
- [ ] Departments and production units have been checked against the client's organization chart.
- [ ] Named platform Super Admin account is held by the platform operator; no shared account was created.
- [ ] Named client Company Admin account is assigned only to the client's company.
- [ ] Factory Managers and Department Managers are assigned to the correct scope.
- [ ] Employee accounts and employee records are linked and spot-checked.
- [ ] Initial credentials were delivered through the approved secure channel; password-reset email delivery was demonstrated or its configuration gap recorded.
- [ ] A second-person review confirmed a client user cannot access another company's records.

## Data and workflows

- [ ] Client-approved master data is loaded and checked for duplicates and correct organization links.
- [ ] Workforce: employee, attendance, shift, and leave flows demonstrated for the relevant roles.
- [ ] Operations: inventory, procurement, production plan, work order, and output flows demonstrated where in scope.
- [ ] Billing: invoice, supplier bill, tax, payment, outstanding, PDF, and report flows demonstrated where in scope.
- [ ] Intelligence and reports: machine, maintenance, quality, energy, and relevant report flows demonstrated where in scope.
- [ ] Client confirms demonstration data is clearly separated from production data or has been removed.

## Production readiness

- [ ] Production checks run with the actual deployment environment; warnings and accepted exceptions recorded.
- [ ] HTTPS certificate, DNS, redirect, allowed hosts, and trusted CSRF origins verified.
- [ ] Static assets load; uploaded media persists across application restart and private downloads are permission-checked.
- [ ] Error and access logs are visible to the support owner; alert routing has been exercised.
- [ ] Backup schedule, off-host retention, and owner are confirmed.
- [ ] Database and media recovery completed in an isolated environment; result and date recorded.
- [ ] Deployment and rollback artifact/location and decision owner are recorded.

## Training, support, and acceptance

- [ ] User training delivered to: ______________________________
- [ ] Administrator training delivered to: _____________________
- [ ] Training materials and recording location (if authorized): ______________
- [ ] Support hours, severity levels, response targets, escalation route, and maintenance window agreed and shared.
- [ ] Open issues, workarounds, owners, and target dates attached.
- [ ] Client acceptance tests were run with client representatives and role-appropriate accounts.
- [ ] Acceptance result: **Accepted / Accepted with listed exceptions / Not accepted**
- [ ] Exceptions and agreed resolution dates: __________________________________________

Client representative: __________________________  Date: ______________

Delivery representative: ________________________  Date: ______________

Acceptance confirms the agreed workflows were demonstrated in the named environment; it does not replace the client's legal, tax, safety, or operational approval processes.
