# Requirements Document

## Introduction

This document defines the requirements for transforming Rabbit from a developer-oriented single-page tool into a product-grade penetration testing platform. The transformation adds user authentication, vulnerability reporting, worker monitoring, project templates, and attack timeline visualization to the existing fact-graph exploration engine.

## Glossary

- **Platform**: The Rabbit web application including backend API and frontend UI
- **Auth_Service**: The authentication and session management subsystem
- **User**: A registered individual with credentials stored in the Platform
- **Session**: A server-side token representing an authenticated User's active login
- **Vulnerability_Report_Engine**: The subsystem that aggregates, categorizes, and exports discovered vulnerabilities from project facts
- **Vulnerability**: A security weakness discovered during a penetration testing project, extracted from project facts
- **Severity_Level**: A classification of vulnerability impact — one of Critical, High, Medium, or Low
- **Worker_Dashboard**: The subsystem that displays real-time status, health, and task history of dispatcher workers
- **Worker**: An AI agent adapter (e.g., Claude Code, Codex, Pi) that executes exploration intents
- **Template_Engine**: The subsystem that manages and applies preset penetration testing project configurations
- **Project_Template**: A predefined configuration containing title, origin, goal, and hints for a specific penetration testing scenario
- **Attack_Timeline**: The subsystem that renders a chronological visualization of the complete attack process for a project
- **Timeline_Event**: A single entry in the attack timeline representing a fact discovery or intent conclusion

## Requirements

### Requirement 1: User Registration

**User Story:** As a new user, I want to create an account with a username and password, so that I can access the Platform securely.

#### Acceptance Criteria

1. WHEN a registration request is submitted with a valid username and password, THE Auth_Service SHALL create a new User record and return a Session token that expires after 24 hours
2. IF a registration request is submitted with a username that already exists (compared case-insensitively), THEN THE Auth_Service SHALL reject the request with a conflict error indicating the username is taken
3. THE Auth_Service SHALL store passwords using bcrypt hashing with a minimum cost factor of 12
4. IF a registration request is submitted with a password shorter than 8 characters or longer than 72 characters, THEN THE Auth_Service SHALL reject the request with a validation error indicating the password length requirement
5. THE Auth_Service SHALL enforce username uniqueness using case-insensitive comparison
6. IF a registration request is submitted with a username shorter than 3 characters, longer than 32 characters, or containing characters other than letters, digits, hyphens, and underscores, THEN THE Auth_Service SHALL reject the request with a validation error indicating the username format requirement
7. IF a registration request is submitted with a missing or empty username or password field, THEN THE Auth_Service SHALL reject the request with a validation error indicating the required fields

### Requirement 2: User Login

**User Story:** As a registered user, I want to log in with my credentials, so that I can access my projects and data.

#### Acceptance Criteria

1. WHEN a registered username and matching password are submitted, THE Auth_Service SHALL create a new Session with an expiration time of 24 hours and return a session token of at least 128 bits of entropy
2. IF the submitted username does not exist or the password does not match, THEN THE Auth_Service SHALL reject the request with a generic authentication error without revealing whether the username or password was incorrect
3. THE Auth_Service SHALL rate-limit login attempts to a maximum of 5 failed attempts per username within a sliding 15-minute window
4. IF the rate limit is exceeded, THEN THE Auth_Service SHALL reject subsequent login attempts for that username with an error indicating the account is temporarily locked, until the 15-minute window expires
5. IF a login attempt is submitted for a disabled or locked account, THEN THE Auth_Service SHALL reject the request with a generic authentication error without distinguishing the reason from an invalid-credentials rejection

### Requirement 3: Session Management

**User Story:** As an authenticated user, I want my session to persist across page reloads, so that I do not need to log in repeatedly.

#### Acceptance Criteria

1. THE Auth_Service SHALL issue session tokens with a configurable expiration period defaulting to 24 hours, where the configurable period is between 1 hour and 720 hours inclusive
2. WHEN a request is made with an expired session token, THE Auth_Service SHALL reject the request with an authentication error and remove the session cookie from the response
3. WHEN a user logs out, THE Auth_Service SHALL invalidate the associated Session within 1 second of receiving the logout request
4. THE Platform SHALL store session tokens in HTTP-only, Secure, SameSite cookies to prevent client-side script access and cross-site transmission over insecure connections
5. WHEN an authenticated request is made, THE Auth_Service SHALL extend the session expiration by the configured period
6. IF a request is made with a session token that is malformed, tampered with, or not found in the session store, THEN THE Auth_Service SHALL reject the request with an authentication error and remove the session cookie from the response

### Requirement 4: Password Change

**User Story:** As an authenticated user, I want to change my password, so that I can maintain account security.

#### Acceptance Criteria

1. WHEN a password change request is submitted with the correct current password and a new password that meets the password policy (minimum 8 characters, at least one uppercase letter, one lowercase letter, one digit, one special character, and maximum 128 characters), THE Auth_Service SHALL update the stored password hash and return a success confirmation to the User
2. IF a password change request is submitted with an incorrect current password, THEN THE Auth_Service SHALL reject the request with an error indicating invalid current credentials and SHALL NOT modify the stored password
3. WHEN a password is changed successfully, THE Auth_Service SHALL invalidate all other active Sessions for that User
4. IF a password change request is submitted with a new password that does not meet the password policy, THEN THE Auth_Service SHALL reject the request with an error indicating the password validation failure and SHALL NOT modify the stored password

### Requirement 5: Route Protection

**User Story:** As a platform operator, I want all application routes to require authentication, so that unauthorized users cannot access the system.

#### Acceptance Criteria

1. WHEN an unauthenticated request is made to any API endpoint not listed in criterion 2, THE Platform SHALL return a 401 status code with a response body containing an error message indicating that authentication is required
2. THE Platform SHALL exempt the login endpoint, registration endpoint, and all paths served under the static assets mount from authentication requirements
3. WHEN an unauthenticated user sends a browser navigation request to a frontend route (the root path or any non-API path that serves HTML), THE Platform SHALL respond with an HTTP 302 redirect to the login page
4. IF a request includes an authentication credential that is expired or malformed, THEN THE Platform SHALL return a 401 status code with a response body containing an error message indicating that the provided credential is invalid

### Requirement 6: Vulnerability Aggregation

**User Story:** As a penetration tester, I want to see all discovered vulnerabilities across my projects in one place, so that I can assess overall security posture.

#### Acceptance Criteria

1. THE Vulnerability_Report_Engine SHALL extract vulnerabilities from project facts whose descriptions contain identified security weaknesses, misconfigurations, or exploitable conditions
2. THE Vulnerability_Report_Engine SHALL categorize each Vulnerability into exactly one Severity_Level (Critical, High, Medium, or Low)
3. WHEN the vulnerability report page is loaded, THE Vulnerability_Report_Engine SHALL display the total count of vulnerabilities grouped by Severity_Level, and for each Vulnerability SHALL display its title, Severity_Level, and associated source project name
4. THE Vulnerability_Report_Engine SHALL associate each Vulnerability with its source project
5. WHEN a project is deleted, THE Vulnerability_Report_Engine SHALL remove all associated vulnerabilities from the report
6. WHEN a new fact containing a security-relevant finding is added to a project, THE Vulnerability_Report_Engine SHALL include the corresponding Vulnerability in the report within 5 seconds without requiring a manual refresh of the data source
7. IF no vulnerabilities exist across any project, THEN THE Vulnerability_Report_Engine SHALL display a count of zero for each Severity_Level

### Requirement 7: Vulnerability Report Filtering

**User Story:** As a penetration tester, I want to filter vulnerabilities by severity and project, so that I can focus on the most critical issues.

#### Acceptance Criteria

1. WHEN a severity filter is applied, THE Vulnerability_Report_Engine SHALL display only vulnerabilities matching the selected Severity_Level, where Severity_Level is one of: Critical, High, Medium, Low, or Informational
2. WHEN a project filter is applied, THE Vulnerability_Report_Engine SHALL display only vulnerabilities from the selected project
3. WHEN multiple filters are applied simultaneously, THE Vulnerability_Report_Engine SHALL display vulnerabilities matching all active filter criteria using AND logic
4. IF no vulnerabilities match the active filter criteria, THEN THE Vulnerability_Report_Engine SHALL display an empty result set with a message indicating that no vulnerabilities match the current filters
5. WHEN all filters are cleared, THE Vulnerability_Report_Engine SHALL display the complete unfiltered list of vulnerabilities within 2 seconds
6. WHEN a filter is applied, THE Vulnerability_Report_Engine SHALL display the filtered results within 3 seconds for result sets of up to 10,000 vulnerabilities

### Requirement 8: Vulnerability Report Export

**User Story:** As a penetration tester, I want to export vulnerability reports, so that I can share findings with stakeholders.

#### Acceptance Criteria

1. WHEN an export is requested in JSON format, THE Vulnerability_Report_Engine SHALL generate a JSON file containing all vulnerabilities matching the user's currently active filter criteria, with each entry including severity, description, and source project name
2. WHEN an export is requested in CSV format, THE Vulnerability_Report_Engine SHALL generate a CSV file with columns for severity, title, description, project name, and discovery date, with one vulnerability per row
3. THE Vulnerability_Report_Engine SHALL include a summary section in exported reports containing total vulnerability counts per Severity_Level, placed as a top-level "summary" object in JSON exports and as header rows preceding the data rows in CSV exports
4. IF an export is requested in a format other than JSON or CSV, THEN THE Vulnerability_Report_Engine SHALL reject the request and return an error message indicating the supported formats
5. IF the current filter criteria match zero vulnerabilities, THEN THE Vulnerability_Report_Engine SHALL generate a valid export file containing only the summary section with all Severity_Level counts set to zero
6. WHEN an export file is generated, THE Vulnerability_Report_Engine SHALL complete the file generation and begin the file download within 30 seconds of the export request

### Requirement 9: Worker Status Display

**User Story:** As a platform operator, I want to see the real-time status of each worker, so that I can monitor system health.

#### Acceptance Criteria

1. THE Worker_Dashboard SHALL display each registered Worker with its name, type, and current status (idle, busy, offline)
2. THE Worker_Dashboard SHALL update worker status at an interval no greater than 5 seconds
3. WHEN a Worker transitions from one status to another, THE Worker_Dashboard SHALL highlight the status change for a minimum duration of 3 seconds
4. THE Worker_Dashboard SHALL display the current task description (truncated to 120 characters maximum) for each busy Worker
5. IF the Worker_Dashboard fails to retrieve status updates for 3 consecutive polling intervals, THEN THE Worker_Dashboard SHALL display a connectivity warning indicating the last successful update time

### Requirement 10: Worker Health Metrics

**User Story:** As a platform operator, I want to see worker health metrics, so that I can identify performance issues.

#### Acceptance Criteria

1. THE Worker_Dashboard SHALL display the total number of tasks completed by each Worker as a whole number
2. THE Worker_Dashboard SHALL display the average task duration for each Worker in seconds, rounded to one decimal place
3. IF a Worker has completed zero tasks, THEN THE Worker_Dashboard SHALL display the average task duration for that Worker as a dash character indicating no data is available
4. THE Worker_Dashboard SHALL display the time since each Worker last reported a heartbeat in seconds, updated at least every 5 seconds
5. IF a Worker has not reported a heartbeat within the configured timeout period, THEN THE Worker_Dashboard SHALL display that Worker with an offline status indicator
6. THE Worker_Dashboard SHALL refresh all displayed metrics at an interval no greater than 10 seconds

### Requirement 11: Worker Task History

**User Story:** As a platform operator, I want to see the recent task history for each worker, so that I can audit worker activity.

#### Acceptance Criteria

1. WHEN a Worker is selected, THE Worker_Dashboard SHALL display the 20 most recent tasks executed by that Worker
2. THE Worker_Dashboard SHALL display each historical task with its project name, task type, description, start time, duration, and outcome status
3. THE Worker_Dashboard SHALL indicate whether each historical task completed successfully, failed, was rejected, or was released without conclusion

### Requirement 12: Project Templates

**User Story:** As a penetration tester, I want to create projects from preset templates, so that I can start common assessments quickly.

#### Acceptance Criteria

1. THE Template_Engine SHALL provide at minimum the following built-in templates: Web Application Assessment, Internal Network Pentest, External Network Pentest, and CTF Challenge
2. WHEN a template is selected, THE Template_Engine SHALL pre-populate the project creation form with the template's title, origin fact, goal fact, and between 1 and 10 initial hints where each hint has a content value and a creator value of "template"
3. THE Platform SHALL allow the user to edit, clear, or replace any pre-populated field value before submitting the project creation form
4. IF the user submits the project creation form with any required field (title, origin, or goal) empty or blank after modification, THEN THE Platform SHALL prevent project creation and indicate which fields are missing
5. WHEN a project is created from a template, THE Platform SHALL create the project using the standard project creation flow with the field values as currently shown in the form, including any user modifications

### Requirement 13: Custom Project Templates

**User Story:** As a penetration tester, I want to save my own project configurations as templates, so that I can reuse them for recurring engagements.

#### Acceptance Criteria

1. WHEN a user saves a custom template, THE Template_Engine SHALL store the template title, origin, goal, and hints associated with that User, where title, origin, and goal are each between 1 and 200 characters
2. THE Template_Engine SHALL display custom templates alongside built-in templates in the template selection interface, with custom templates visually labeled as user-created
3. WHEN a user selects a custom template, THE Template_Engine SHALL pre-fill the new project form with the stored title, origin, goal, and hints from that template
4. WHEN a user deletes a custom template, THE Template_Engine SHALL remove the template permanently and it SHALL no longer appear in the template selection interface
5. IF a user attempts to delete a template they did not create, THEN THE Template_Engine SHALL reject the request and display an error message indicating insufficient ownership
6. THE Template_Engine SHALL limit each User to a maximum of 50 custom templates
7. IF a user attempts to save a custom template and already has 50 custom templates stored, THEN THE Template_Engine SHALL reject the save request and display an error message indicating the template limit has been reached

### Requirement 14: Attack Timeline Visualization

**User Story:** As a penetration tester, I want to see a chronological timeline of the attack process, so that I can understand the progression of the engagement.

#### Acceptance Criteria

1. WHEN a project is viewed, THE Attack_Timeline SHALL render all concluded intents and discovered facts in chronological order, sorting by creation timestamp with declaration order as tiebreaker for events sharing the same timestamp
2. THE Attack_Timeline SHALL visually distinguish between event types (fact discoveries, intent declarations, intent conclusions, and project completion) using a distinct dot color and labeled badge per event type
3. THE Attack_Timeline SHALL display the description, formatted timestamp, and event-type badge for each Timeline_Event
4. THE Attack_Timeline SHALL display the actor (Worker or creator name) that executed each intent alongside the intent's Timeline_Event entry
5. IF the project contains no timeline events, THEN THE Attack_Timeline SHALL display an empty-state message indicating no activity has been recorded
6. WHEN a new intent is concluded or a new fact is discovered while the project is being viewed, THE Attack_Timeline SHALL append the new Timeline_Event to the chronological list within the next polling refresh cycle without requiring a manual page reload

### Requirement 15: Attack Timeline Navigation

**User Story:** As a penetration tester, I want to navigate the attack timeline interactively, so that I can focus on specific phases of the engagement.

#### Acceptance Criteria

1. WHEN a Timeline_Event associated with a graph node (fact or intent) is clicked, THE Attack_Timeline SHALL highlight and center the corresponding node in the project graph view within 1 second
2. IF a Timeline_Event is clicked that has no associated graph node, THEN THE Attack_Timeline SHALL visually select the timeline entry without altering the graph view
3. WHILE the timeline contains more events than fit in the visible panel area, THE Attack_Timeline SHALL provide vertical scrolling to access all events in chronological order
4. WHEN new events occur in an active project, THE Attack_Timeline SHALL append the new events to the end of the list within 5 seconds without requiring a page reload or losing the user's current scroll position
5. WHEN a Timeline_Event is clicked, THE Attack_Timeline SHALL visually distinguish the selected entry from unselected entries using a distinct background or border style

### Requirement 16: Navigation and Layout

**User Story:** As a user, I want a clear navigation structure, so that I can access all platform features easily.

#### Acceptance Criteria

1. THE Platform SHALL display a navigation sidebar on every authenticated view with links to: Projects, Vulnerability Reports, Worker Dashboard, and Templates
2. WHEN the user is on a mobile viewport (width less than 768 pixels), THE Platform SHALL collapse the navigation sidebar into a hamburger menu that can be toggled open and closed
3. THE Platform SHALL display the currently authenticated username (truncated to 20 characters with an ellipsis if longer) in the navigation header
4. WHEN the user activates the logout action in the navigation header, THE Platform SHALL end the user session and redirect the user to the login page
5. THE Platform SHALL visually distinguish the navigation link corresponding to the currently active view from the other navigation links
