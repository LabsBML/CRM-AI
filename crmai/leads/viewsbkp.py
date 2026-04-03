from django.shortcuts import render
import requests
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from supabase import create_client
import json
from django.contrib.auth.decorators import login_required
from datetime import datetime
import re
from django.http import HttpResponse
from django.core.mail import EmailMessage 

@login_required
def lead_submissions(request):
    # 1. Capture Pagination & Filters
    try:
        page = int(request.GET.get('page', 1))
    except ValueError:
        page = 1

    limit = 20
    offset = (page - 1) * limit

    # Get filter values
    email_q = request.GET.get('email', '').strip()
    phone_q = request.GET.get('phone', '').strip()
    student_q = request.GET.get('student_name', '').strip()
    parent_q = request.GET.get('parent_name', '').strip()
    source_q = request.GET.get('source', '').strip()
    event_q = request.GET.get('event', '').strip()

    # 2. Setup Base Request
    url = f"{settings.SUPABASE_URL}/rest/v1/lead_submissions"
    headers = {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
    }

    # --- NEW TOTAL COUNT LOGIC ---
    count_params = {"merged_into": "is.null"}
    if email_q: count_params['primary_email'] = f"ilike.*{email_q}*"
    if phone_q: count_params['primary_phone'] = f"ilike.*{phone_q}*"
    if student_q: count_params['student_name'] = f"ilike.*{student_q}*"
    if parent_q: count_params['parent_name'] = f"ilike.*{parent_q}*"
    if source_q: count_params['source_platform'] = f"ilike.*{source_q}*"
    if event_q: count_params['source_event'] = f"ilike.*{event_q}*"

    # Fetch total count using a HEAD request for efficiency
    count_res = requests.head(url, headers={**headers, "Prefer": "count=exact"}, params=count_params)
    total_records = 0
    total_pages = 1
    if count_res.status_code in [200, 206]:
        content_range = count_res.headers.get("Content-Range", "")
        if "/" in content_range:
            total_records = int(content_range.split("/")[-1])
            total_pages = (total_records + limit - 1) // limit

    params = {
        "select": "*",
        "merged_into": "is.null",
        "order": "submitted_at.desc",
        "offset": offset,
        "limit": limit + 1
    }

    # Apply Filters
    if email_q: params['primary_email'] = f"ilike.*{email_q}*"
    if phone_q: params['primary_phone'] = f"ilike.*{phone_q}*"
    if student_q: params['student_name'] = f"ilike.*{student_q}*"
    if parent_q: params['parent_name'] = f"ilike.*{parent_q}*"
    if source_q: params['source_platform'] = f"ilike.*{source_q}*"
    if event_q: params['source_event'] = f"ilike.*{event_q}*"

    response = requests.get(url, headers=headers, params=params)

    leads = []
    has_next = False

    if response.status_code == 200:
        data = response.json()
        if len(data) > limit:
            has_next = True
            leads = data[:limit]
        else:
            leads = data

        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

        for lead in leads:
            identifiers = set()
            if lead.get('primary_email'): identifiers.add(lead['primary_email'])
            if lead.get('student_email'): identifiers.add(lead['student_email'])
            if lead.get('parent_email'): identifiers.add(lead['parent_email'])

            # --- ROBUST PHONE HANDLING FIX ---
            raw_phone = lead.get('primary_phone')
            phones_list = []

            if isinstance(raw_phone, list):
                phones_list = raw_phone
            elif isinstance(raw_phone, str):
                try:
                    parsed = json.loads(raw_phone.replace("'", '"'))
                    phones_list = parsed if isinstance(parsed, list) else [str(parsed)]
                except:
                    phones_list = [raw_phone] if raw_phone.strip() else []
            elif raw_phone is not None:
                phones_list = [str(raw_phone)]
            
            search_terms = []
            for email in identifiers:
                if email:
                    search_terms.append(f"primary_email.eq.{email}")
                    search_terms.append(f"student_email.eq.{email}")
                    search_terms.append(f"parent_email.eq.{email}")

            for p in phones_list:
                clean_p = ''.join(filter(str.isdigit, str(p)))
                if clean_p:
                    search_terms.append(f"primary_phone.ilike.*{clean_p}*")
            # --- END PHONE FIX ---

            if search_terms:
                or_filter = ",".join(search_terms)
                dup_check = supabase.table("lead_submissions").select("id", count="exact").or_(or_filter).neq("id",lead['id']).execute()
                lead['is_redundant'] = (dup_check.count > 0)
                lead['dup_count'] = dup_check.count
            else:
                lead['is_redundant'] = False

        for lead in leads:
            if lead.get('submitted_at'):
                try:
                    dt_str = lead['submitted_at'].replace('Z', '+00:00')
                    dt = datetime.fromisoformat(dt_str)
                    lead['display_date'] = dt.strftime('%d/%m/%y %H:%M')
                except:
                    lead['display_date'] = lead['submitted_at']
            else:
                lead['display_date'] = "-"

    start_page = max(1, page - 2)
    end_page = min(total_pages, page + 2)
    page_range = range(start_page, end_page + 1)

    return render(request, "leads/lead_submissions.html", {
        "leads": leads,
        "page": page,
        "total_pages": total_pages,
        "total_records": total_records,
        "page_range": page_range,
        "has_next": has_next,
        "has_prev": page > 1,
        "filters": {
            "email": email_q, "phone": phone_q,
            "student_name": student_q, "parent_name": parent_q,
            "source": source_q, "event": event_q
        }
    })
@csrf_exempt
def send_merge_to_n8n(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        body = json.loads(request.body)
        submission_ids = body.get("submission_ids", [])

        if len(submission_ids) < 2:
            return JsonResponse({"error": "Select at least 2 records to merge"}, status=400)

        payload = {
            "action": "merge_leads",
            "submission_ids": submission_ids,
            "requested_by": "django_ui",
        }

        response = requests.post(
            settings.N8N_MERGE_WEBHOOK_URL,
            json=payload,
            timeout=15,
        )

        if response.status_code != 200:
            return JsonResponse({"error": "n8n merge failed", "details": response.text}, status=500)

        return JsonResponse({"status": "merge_requested", "n8n_response": response.json()})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
        
@csrf_exempt
def save_remark(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        data = json.loads(request.body)
        lead_id = data.get("id")
        remark = (data.get("remark") or "").strip()

        if not lead_id:
            return JsonResponse({"error": "Missing lead id"}, status=400)

        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

        update_data = {"remark": remark if remark else None}

        supabase.table("lead_submissions") \
            .update(update_data) \
            .eq("id", lead_id) \
            .execute()

        return JsonResponse({"success": True})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
        

@csrf_exempt
def update_email_source(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        data = json.loads(request.body)
        lead_id = data.get("id")
        source_type = data.get("type")  # 'S', 'P', or 'UK'
        email_from_ui = data.get("email")

        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        update_data = {}

        # Default display_email back to what was sent from UI
        display_email = email_from_ui 

        if source_type == 'UK':
            # Database: Clear primary_email link
            # UI: We keep the email text as is (per your request)
            update_data["primary_email"] = None
        
        elif source_type == 'S':
            update_data["student_email"] = email_from_ui
            update_data["primary_email"] = email_from_ui
            
        elif source_type == 'P':
            update_data["parent_email"] = email_from_ui
            update_data["primary_email"] = email_from_ui

        if update_data or source_type == 'UK':
            supabase.table("lead_submissions").update(update_data).eq("id", lead_id).execute()
            # We return the email_from_ui so the span text doesn't change to "Unknown"
            return JsonResponse({"success": True, "new_email": display_email})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
        
        
        
@csrf_exempt
def update_lead_status(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            lead_id = data.get('id')
            status_value = data.get('status')
            
            # Use the Supabase client
            supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
            
            # Update the record in the lead_submissions table
            supabase.table("lead_submissions") \
                .update({"status": status_value}) \
                .eq("id", lead_id) \
                .execute()
            
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@csrf_exempt
def delete_lead(request):
    """
    Deletes a lead record from the database and screen accurately using its ID.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        data = json.loads(request.body)
        lead_id = data.get("id")

        if not lead_id:
            return JsonResponse({"error": "Missing lead id"}, status=400)

        # Connect to Supabase using existing settings
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

        # Perform the deletion
        supabase.table("lead_submissions") \
            .delete() \
            .eq("id", lead_id) \
            .execute()

        return JsonResponse({"success": True})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
        
        
        
@csrf_exempt # Add this decorator so the request isn't blocked
def update_intent_level(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            lead_id = data.get('id')
            intent_value = data.get('intent')
            
            # Connect to Supabase (not Django ORM)
            supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
            
            # Update the specific record
            supabase.table("lead_submissions") \
                .update({"intent_level": intent_value}) \
                .eq("id", lead_id) \
                .execute()
            
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})
        
        
        
@csrf_exempt
def update_location(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)
    try:
        data = json.loads(request.body)
        lead_id = data.get("id")
        city = data.get("city")
        state = data.get("state")

        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        
        # Prepare data for update
        update_data = {}
        if city is not None: update_data["city"] = city.strip()
        if state is not None: update_data["state"] = state.strip()

        if update_data:
            supabase.table("lead_submissions").update(update_data).eq("id", lead_id).execute()

        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
        
        
@csrf_exempt
def update_call_status(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            lead_id = data.get('id')
            call_val = data.get('call_status')
            supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
            supabase.table("lead_submissions").update({"call_status": call_val}).eq("id", lead_id).execute()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
            
            

def parse_mail_content(raw_text):
    """
    Regex to extract Subject and Body.
    Looks for 'Subject: ...' at the start and captures the rest as body.
    """
    if not raw_text:
        return {"subject": "", "body": ""}
    
    subject_match = re.search(r"Subject:\s*(.*)", raw_text, re.IGNORECASE)
    subject = subject_match.group(1).strip() if subject_match else "No Subject"
    
    # Remove the Subject line to get the body
    body = re.sub(r"Subject:.*", "", raw_text, flags=re.IGNORECASE).strip()
    
    return {"subject": subject, "body": body}

import re

def clean_data(value):
    """Sanitizes data to prevent UnicodeDecodeErrors and handle formatting."""
    if value is None: return ""
    # Force to string and ignore non-utf8 bytes
    return str(value).encode('utf-8', 'ignore').decode('utf-8')

#@login_required
#def student_profile(request, lead_id):
#    supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
#    
#    response = supabase.table("lead_submissions").select("*").eq("id", lead_id).execute()
#    if not response.data:
#        return render(request, "404.html")
#    
#    raw_lead = response.data[0]
#   lead = {key: clean_data(val) for key, val in raw_lead.items()}
#    
#    lead['parent_email'] = clean_data(raw_lead.get('parent_email'))
#    lead['student_email'] = clean_data(raw_lead.get('student_email'))
#    
#    for i in range(1, 7):
#        content_field = f'mail_{i}_content'
#        raw_text = raw_lead.get(content_field) or raw_lead.get(f'mail_{i}')
#        lead[f'mail_{i}_parsed'] = parse_mail(raw_text)
#
#    # FINAL RETURN RE-ADDED: This fixes the ValueError
#   return render(request, "leads/student_profile.html", {"lead": lead})
#
@login_required
def student_profile(request, lead_id):
    supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    
    # 1. Fetch the base lead record from lead_submissions
    response = supabase.table("lead_submissions").select("*").eq("id", lead_id).execute()
    if not response.data:
        # Fallback if lead_id doesn't exist
        return HttpResponse("Lead not found or already deleted.", status=404)
    
    raw_lead = response.data[0]
    status = (raw_lead.get('status') or "").lower()

    # ---------------------------------------------------------
    # ROUTE A: THE STUDENT DASHBOARD (For "Closed" Leads)
    # ---------------------------------------------------------
    if status == 'closed':
        email = raw_lead.get('primary_email')
        # Fetch from your new Unified View for aggregated data
        view_res = supabase.table("bml_unified_view").select("*").eq("student_email", email).execute()
        
        # Use the unified view data if found, otherwise use raw lead data
        student_data = view_res.data[0] if view_res.data else raw_lead
        
        # Clean data: Replace None with "" so textboxes are blank, not "None"
        cleaned_student = {k: (v if v is not None else "") for k, v in student_data.items()}
        
        return render(request, "leads/student_dashboard.html", {
            "student": cleaned_student,
            "lead_id": lead_id
        })

    # ---------------------------------------------------------
    # ROUTE B: THE LEAD PROFILE (For all other statuses)
    # ---------------------------------------------------------
    # This is the exact logic from your current views.py

    # Prepare lead dictionary for the template
    lead = {key: clean_data(val) for key, val in raw_lead.items()}
    
    # Explicitly handle common fields
    lead['parent_email'] = clean_data(raw_lead.get('parent_email'))
    lead['student_email'] = clean_data(raw_lead.get('student_email'))
    
    # Process the 6 mail sequences exactly as before
    for i in range(1, 7):
        content_field = f'mail_{i}_content'
        # Check both the content field and the legacy mail_{i} field
        raw_text = raw_lead.get(content_field) or raw_lead.get(f'mail_{i}')
        lead[f'mail_{i}_parsed'] = parse_mail(raw_text)

    return render(request, "leads/student_profile.html", {"lead": lead})
def parse_mail(text):
    if not text: return {"subject": "No Subject", "body": ""}
    
    # 1. Extract Subject
    sub_match = re.search(r"Subject:\s*(.*)", text, re.I)
    subject = sub_match.group(1).strip() if sub_match else "No Subject"
    
    # 2. Get the body
    body = re.sub(r"Subject:.*", "", text, count=1, flags=re.I).lstrip('\n\r')
    
    # 3. Handle Hybrid Content (Text + HTML)
    if bool(re.search(r'<[a-z][\s\S]*>', body, re.IGNORECASE)):
        # Split the body into the 'Injected Text' and 'HTML Template'
        # We find the first occurrence of an HTML tag (like <div, <table, <p)
        parts = re.split(r'(?=<[a-z])', body, maxsplit=1, flags=re.I)
        
        if len(parts) > 1:
            injected_text = parts[0]
            html_template = parts[1]
            
            # Convert newlines to <br> ONLY in the injected text part
            injected_text = injected_text.replace('\n', '<br>')
            
            # Minify only the HTML template part to prevent gaps
            html_template = re.sub(r'>\s+<', '><', html_template)
            html_template = html_template.replace('\n', '').replace('\r', '')
            
            body = injected_text + html_template
        else:
            # It's pure HTML, just minify it
            body = re.sub(r'>\s+<', '><', body)
            body = body.replace('\n', '').replace('\r', '')
            
    return {"subject": subject, "body": body}
    

@csrf_exempt
def send_zoho_mail(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        data = json.loads(request.body)
        lead_id = data.get("lead_id") # Must add this
        index = data.get("index")
        body_content = data.get("body", "")

        # Check if there is plain text before the first HTML tag
        # If the string starts with text and then hits a tag, we need to ensure 
        # that leading text has its formatting preserved.
        if not body_content.strip().startswith('<'):
            # Convert any remaining literal newlines to <br> 
            # (Safety catch for mixed content)
            body_content = body_content.replace('\n', '<br>')

        email = EmailMessage(
            subject=data.get("subject"),
            body=body_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[data.get("to")],
            cc=[c.strip() for c in data.get("cc", "").split(",") if c.strip()],
        )
        email.content_subtype = "html" 
        email.send(fail_silently=False)

        # Update Supabase
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        sent_column = f"mail_{index}_sent"
        supabase.table("lead_submissions").update({sent_column: True}).eq("id", lead_id).execute()
        
        return JsonResponse({"success": True, "message": f"Sequence {index} sent successfully."})

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)
        
        
        
        
@csrf_exempt
@login_required
def update_student_data(request):
    """
    Handles inline CRUD from the Student Dashboard.
    Updates induction, midterm, or endterm tables based on the 'table' parameter.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        data = json.loads(request.body)
        email = data.get("email")
        table_name = data.get("table")
        column_name = data.get("column")
        new_value = data.get("value")

        if not all([email, table_name, column_name]):
            return JsonResponse({"error": "Missing required fields"}, status=400)

        # Convert empty strings to None so they appear as NULL in Postgres
        save_value = new_value.strip() if new_value and str(new_value).strip() else None

        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        
        # We use email as the unique identifier for these three tables
        supabase.table(table_name).update({column_name: save_value}).eq("email", email).execute()

        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
        
        
# views.py

@login_required
def insights_dashboard(request):
    # Capture Filters (Same as lead_submissions)
    email_q = request.GET.get('email', '').strip()
    student_q = request.GET.get('student_name', '').strip()

    # Setup Supabase Request for the Unified View
    url = f"{settings.SUPABASE_URL}/rest/v1/bml_unified_view"
    headers = {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
    }

    # Parameters: Selecting key fields for the list and ordering by latest records
    params = {
        "select": "student_email,student_name,induction_grade,midterm_phone,project_name,rating_overall_experience,induction_submitted_at",
        "order": "induction_submitted_at.desc.nullslast",
        "limit": 100 
    }

    if email_q: params['student_email'] = f"ilike.*{email_q}*"
    if student_q: params['student_name'] = f"ilike.*{student_q}*"

    response = requests.get(url, headers=headers, params=params)
    students = response.json() if response.status_code == 200 else []

    # Map the induction_submitted_at for clean display
    for s in students:
        if s.get('induction_submitted_at'):
            try:
                dt = datetime.fromisoformat(s['induction_submitted_at'].replace('Z', '+00:00'))
                s['display_date'] = dt.strftime('%d/%m/%y %H:%M')
            except:
                s['display_date'] = s['induction_submitted_at']

    return render(request, "leads/insights_dashboard.html", {
        "students": students,
        "filters": {"email": email_q, "student_name": student_q}
    })
    
    
# views.py

@login_required
def student_dashboard_view(request, email):
    """
    Fetches student data from the unified view using email 
    and renders the student_dashboard.html
    """
    url = f"{settings.SUPABASE_URL}/rest/v1/bml_unified_view"
    headers = {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
    }
    
    # Filter the view by the student_email column
    params = {
        "student_email": f"eq.{email}",
        "select": "*"
    }
    
    response = requests.get(url, headers=headers, params=params)
    data = response.json()

    if not data or len(data) == 0:
        # Handle case where email doesn't exist in the unified view
        return render(request, "404.html", {"message": "Student record not found"}, status=404)

    # Extract the first matching record
    student_data = data[0]

    return render(request, "leads/student_dashboard.html", {
        "student": student_data,
        "email": email
    })