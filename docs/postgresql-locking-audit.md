# PostgreSQL locking audit — 2026-10-01

Scope: project Python code, excluding virtualenv; follow filters, related loading,
model ordering and managers, not just literal lock calls. Models in these paths
use ordinary Django managers; none inject hidden select_related joins.
18 existing lock call sites; 19 after adding the explicit profile lock below.

## Confirmed failures before this change

`POST /crm/appointments/<id>/no-show/`:

```python
Appointment.objects.select_for_update().get(
    pk=pk, status__in=["new", "confirmed"], crm_order__isnull=True,
)
```

The reverse optional OneToOne `CRMOrder.source_appointment` generates:

```sql
SELECT ... FROM repairs_appointment
LEFT OUTER JOIN crm_crmorder
  ON repairs_appointment.id = crm_crmorder.source_appointment_id
WHERE ... AND crm_crmorder.id IS NULL
FOR UPDATE
```

PostgreSQL raises `NotSupportedError: FOR UPDATE cannot be applied to the nullable
side of an outer join`. There is no CRMOrder row to lock for an eligible no-show.
Use `FOR UPDATE OF repairs_appointment`; preserve the status/absence conditions.
Conversion also locks that Appointment row and checks for an existing order.

`POST /crm/orders/new/` with an Appointment:
`select_related("account__account_profile")` introduces nullable account/profile
joins and raises the same exception even for an anonymous Appointment.
Lock Appointment only in this query. If a profile exists, lock it separately
before reading/updating its CRM client link. Account is read-only here.

Both failures were reproduced with real PostgreSQL in an isolated local cluster.

## Inventory (all 19 current call sites)

| Location / operation | Joined rows / nullable side | Decision |
|---|---|---|
| accounts/services.py: verify_phone_code | No JOIN; user filter uses FK id | Unchanged |
| finance/forms.py: RepairFinanceForm.save, selected PartItem | No JOIN; pk filter | Unchanged |
| finance/services.py: close_payroll_period | No JOIN | Unchanged |
| finance/views.py: master_access_create | No JOIN, despite nullable employee.user field | Unchanged |
| notify_tg/management/commands/run_tg_bot.py: assign referral code | No JOIN | Unchanged |
| repairs/booking_capacity.py: lock_day | No JOIN; called inside booking transaction before fresh availability check | Unchanged |
| repairs/signals.py: referral credit owner | No JOIN, inside atomic block | Unchanged |
| crm/services.py: issue_order / CRMOrder | Nullable employee, finance_repair; already OF self | Previous fix retained |
| crm/services.py: issue_order / RepairFinance | No JOIN; explicit separate lock | Unchanged |
| crm/services.py: install_part / CRMOrder | Nullable employee; already OF self | Previous fix retained |
| crm/services.py: install_part / PartItem | receipt, part, supplier all mandatory INNER JOIN | Unchanged; existing related locks retained |
| crm/services.py: return_part / CRMOrder | No JOIN | Unchanged |
| crm/services.py: return_part / CRMOrderPartUsage | All selected relations mandatory; already OF self | Previous lock strategy retained |
| crm/services.py: return_part / PartItem | No JOIN | Unchanged |
| crm/services.py: return_part / RepairPart | FK filters use ids, ordering uses own fields | Unchanged |
| crm/views.py: work_queue_take | Status filter, no JOIN | Unchanged |
| crm/views.py: order_create / Appointment | Nullable account/account_profile | Fixed: OF self |
| crm/views.py: order_create / AccountProfile | No JOIN | Added explicit lock for mutable profile |
| crm/views.py: appointment_no_show | LEFT JOIN from crm_order__isnull filter | Fixed: OF self |

No mass replacement. The remaining plain FOR UPDATE calls have no nullable joined
tables. All are evaluated inside an atomic block (including callers for lock_day).
Status changes do not add a select_for_update call. Salary payouts likewise have
no additional hidden lock QuerySet; payroll closing locks its period explicitly.
This audit addresses nullable JOIN correctness, not a redesign of concurrency
across all services.

## Behaviour preserved

No-show is POST-only, CSRF-protected, and uses existing CRM permissions. The UI
uses an ordinary POST form, not HTMX. Successful POST redirects; repeated POST
and converted appointments return 404 without mutation. Existing save signals
remain unchanged; no new event or notification is invented.

Tests cover real PostgreSQL SQL/lock scope, profile conversion, no-show capacity
release, repeat requests, converted records, approved master and CSRF. Existing
full-suite tests additionally exercise booking, issue, stock return, status,
Telegram and slot performance.
