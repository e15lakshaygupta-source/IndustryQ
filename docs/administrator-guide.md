# INDUSTRIQ administrator guide

## Before onboarding a client

Confirm the production deployment is using PostgreSQL, `DEBUG=0`, the client's exact domain and trusted HTTPS origins, secure secret storage, persistent private media storage, an operational email provider for password recovery, and monitored off-host backups. Follow [Production deployment](../DEPLOYMENT.md) and [Backup and recovery](backup-recovery.md). Do not put client production data in a development or demonstration environment.

Decide which named person will own the initial platform administrator account, client administrator accounts, support contact, backup alerts, and emergency access. Avoid shared administrator logins.

## Set up the client organization

Sign in using a platform Super Admin account, then open **Organization**.

1. Create the client company. Enter the legal name, registration details, contact information, and active status supplied by the client.
2. Create each factory under the correct company. Enter a unique factory code and verify its location, state code, GSTIN, capacity, and manager details where applicable.
3. Create departments under each factory and production units under the correct department. Check the hierarchy before creating dependent workforce, machine, inventory, or billing records.
4. Check the organization overview and verify the company/factory/department/unit counts and relationships.

Organization management is role-scoped. Company Admins manage their assigned company and factories; Factory and Department Managers are restricted to their assigned scope. Only an authorized Super Admin should create or change company-level records.

## Create the first client administrator

1. Open **Create user** and create a named account for the client's designated Company Admin. For an already existing account, use **Manage users & roles** to assign its role and scope.
2. Assign the `Company Admin` role and the new company. Confirm the role assignment before sharing login instructions.
3. Sign in or use an approved impersonation-free account check to verify that the Company Admin can see the intended company and cannot see another client's records.
4. Share credentials through the approved secure channel and require the user to change the initial password. Configure email delivery before relying on password-reset emails.

Application roles and Django site-superuser accounts are different. Create Django `is_superuser` accounts only for platform operators using the deployment's controlled Django admin procedure. A client Company Admin should not receive platform-wide Django admin access.

## Onboard managers and employees

- Create manager and employee user accounts from **Create user**, or manage existing profiles in **Manage users & roles**.
- Assign each account to its correct company, factory, and department. The role must be no broader than the person's operational responsibility.
- Create the employee record in Workforce with its employee ID, name, factory, department, designation, joining date, and employment status.
- Link the employee record to the correct user account. Confirm the employee portal displays only that employee's personal data and assigned records.
- Create shifts and assign employees only after the hierarchy and employee records are checked. Review attendance, leave approval, and notification behavior with the manager.

## Access, audit, and data handling

- Review role assignments periodically and remove access promptly when responsibilities change or a person leaves.
- Use the Audit History page to review important account and business actions. Keep named accounts; never share an administrator account.
- Integration API keys are factory-scoped and shown once. Store them in the client's secret manager, rotate by creating a replacement and revoking the old key, and never paste them into tickets or logs.
- Upload only approved files. Confirm private downloads are opened through the application and are not publicly exposed by the host's media configuration.
- Do not use production data in training or demonstrations unless the client explicitly authorizes it and the environment is access-controlled.

## Backup, support, and maintenance

Follow the deployment backup schedule and [recovery instructions](backup-recovery.md). Verify database and media recovery in an isolated environment and record the result. Agree with the client on support hours, severity definitions, response targets, escalation contacts, maintenance windows, release notices, backup retention, and recovery objectives; the repository does not define a support SLA or ticketing integration.

For releases, follow [Production deployment](../DEPLOYMENT.md), run the production checks, retain the previous release artifact, and use backward-compatible database migrations. Record the release and rollback decision in the deployment log.
