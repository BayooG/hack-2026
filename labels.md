# Labels

What each Gmail label is for. The classifier reads this to pick labels and sub-labels.

- One `## <label name>` section per Gmail label. The emoji is optional; names match by letters only.
- `Sub-labels:` says when the label gets sub-labels. `never` is enforced in code; anything else guides the classifier.
- `Cleanup:` (optional) says when a sub-label is done and can be deleted. `never` is enforced in code; anything else guides the cleanup model.
- The rest of the section says what belongs under the label and what doesn't.
- The labels themselves come from Gmail. A Gmail label missing here still works, without a description.

## 🎓 University
Sub-labels: one per course or thesis
Cleanup: after the course's exam period; never the thesis
Courses, exams, thesis, professors and TU Berlin admin.
Not PhD applications (phd admissions).

## 🔐 Authorisations
Sub-labels: never
Things you act on to get or grant access: sign-in codes, 2FA codes, password resets,
account verification, invitations to join an account, team or project.
Not warnings about your account (Alerts).

## 💼 Job Applications
Sub-labels: one per company and role
Cleanup: once rejected, withdrawn, or silent for two months
Your own applications: recruiters, interviews, assessments, offers and rejections.
Not job alerts or job listing emails (Newsletters).

## 🔔 Alerts
Sub-labels: never
Security alerts and account notices: new sign-ins, suspicious activity, password or
account changes, service and policy notices from companies you have an account with.
Not job alerts (Newsletters), not codes you need to sign in (Authorisations).

## ✈️ Travel
Sub-labels: one per trip
Cleanup: after the trip is over
Flights, trains, hotels, bookings and itineraries.
Not events in another city, unless the email is about booking the travel.

## 🛍️ Shopping
Sub-labels: never
Orders, shipping, receipts and returns.
Not store marketing (Newsletters), not invoices or bills you have to pay (Finance).

## 📰 Newsletters
Sub-labels: never
Subscribed content, digests, marketing, and job alerts or job listing emails.

## 📅 Events
Sub-labels: one per event
Cleanup: after the event date
Invitations, tickets and schedules for things you attend: meetups, hackathons,
conferences, talks, parties.
Not job interviews (Job Applications).

## 💳 Finance
Sub-labels: never
Money: invoices and bills to pay, payment confirmations, PayPal and bank emails about
money sent or received, statements, refunds, taxes, rent, contracts and subscription billing.
Not the receipt for an online order (Shopping), not security warnings from your bank (Alerts),
not health insurance (Medical).

## 🩺 Medical
Sub-labels: one per doctor or practice
Cleanup: never
Doctors, dentists, appointments, prescriptions, test results, health insurance and
medical bills.

## phd admissions
Sub-labels: one per programme
Cleanup: once a decision has come in or the cycle has closed
PhD applications, programmes and potential supervisors.
Not regular job applications (Job Applications).
