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
        "source_event": "not.in.(MEETING_STARTED,MEETING_ENDED)",
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
            
            supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
            
            # 1. Update the specific lead first
            supabase.table("lead_submissions").update({"status": status_value}).eq("id", lead_id).execute()

            # 2. Duplicate Sync Logic
            if status_value == "Closed":
                # Fetch the master data for the clicked lead to find its "twins"
                curr = supabase.table("lead_submissions").select("primary_email, student_name").eq("id", lead_id).execute()
                
                if curr.data:
                    email = curr.data[0].get('primary_email', '').strip()
                    name = curr.data[0].get('student_name', '').strip()

                    or_filters = []
                    # 'ilike' handles case-insensitivity (e.g., 'Amaan' matches 'amaan')
                    if email: or_filters.append(f"primary_email.ilike.{email}")
                    if name:  or_filters.append(f"student_name.ilike.{name}")
                    
                    if or_filters:
                        # Update every record that matches the email OR the name
                        supabase.table("lead_submissions")\
                            .update({"status": "Closed"})\
                            .or_(",".join(or_filters))\
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
        view_res = supabase.table("bml_unified_view3").select("*").eq("student_email", email).execute()
        
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
        
        
        
        
import uuid
import logging

logger = logging.getLogger(__name__)

@csrf_exempt
@login_required
def update_student_data(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Invalid Method"}, status=405)

    try:
        data = json.loads(request.body)
        table = data.get("table")
        col = data.get("column")
        val = data.get("value")
        email = data.get("email", "").strip()

        print(f"\n--- DEBUG SAVE START ---")
        print(f"Target Table: {table} | Target Col: {col} | Email: {email}")

        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        
        # --- NEW: STEP 0 - ENSURE PARENT RECORD EXISTS IN bml_students ---
        # This prevents Foreign Key constraint errors (Code 23503)
        student_check = supabase.table("bml_students").select("email").eq("email", email).execute()
        if not student_check.data:
            print(f"DEBUG: Student {email} missing from bml_students. Seeding parent...")
            # Fetch name from lead_submissions to ensure the record is complete
            lead_info = supabase.table("lead_submissions").select("student_name").eq("primary_email", email).execute()
            s_name = lead_info.data[0].get("student_name") if lead_info.data else "New Student"
            
            supabase.table("bml_students").insert({
                "email": email,
                "name": s_name
            }).execute()
        # ----------------------------------------------------------------

        # Mapping table specifics based on your CSV export
        search_col = "primary_email" if table == "lead_submissions" else "email"
        
        # Midterm uses 'submissionid' (no underscore), others use 'submission_id'
        id_col = "submissionid" if "midterm" in table else "submission_id"

        # 1. FETCH existing record to merge
        existing = supabase.table(table).select("*").eq(search_col, email).execute()
        
        if existing.data:
            payload = existing.data[0]
            print(f"DEBUG: Found existing record in {table}. ID: {payload.get(id_col)}")
        else:
            print(f"DEBUG: No record in {table}. Initializing new row.")
            payload = {
                search_col: email,
                id_col: f"manual_{uuid.uuid4().hex[:8]}"
            }

        # 2. Update the payload with the new value
        payload[col] = val

        # 3. UPSERT to Database
        # We use on_conflict on the search_col (email)
        save_res = supabase.table(table).upsert(payload, on_conflict=search_col).execute()
        
        # 4. VERIFY: Read it back immediately
        verify = supabase.table(table).select(col).eq(search_col, email).execute()
        db_val = verify.data[0].get(col) if verify.data else None

        print(f"DEBUG: Verified DB Value: '{db_val}' (Expected: '{val}')")

        if str(db_val) != str(val):
            return JsonResponse({"success": False, "error": f"Value mismatch. DB has: {db_val}"})

        return JsonResponse({"success": True, "verified_val": db_val})

    except Exception as e:
        print(f"DEBUG ERROR: {str(e)}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)

@login_required
def insights_dashboard(request):
    # 1. Capture All Filters
    email_q = request.GET.get('email', '').strip()
    student_q = request.GET.get('student_name', '').strip()
    grade_q = request.GET.get('grade', '').strip()
    sort_q = request.GET.get('sort', 'newest') # Default to newest

    # 2. Map Sorting Options to Supabase Order Strings
    sort_map = {
        'newest': 'induction_submitted_at.desc.nullslast',
        'oldest': 'induction_submitted_at.asc.nullslast',
        'name_asc': 'student_name.asc',
        'name_desc': 'student_name.desc',
        'rating_high': 'rating_overall_experience.desc.nullslast',
        'grade_high': 'induction_grade.desc.nullslast',
        'grade_low': 'induction_grade.asc.nullslast',
    }
    order_string = sort_map.get(sort_q, sort_map['newest'])

    # 3. Setup Supabase Request
    url = f"{settings.SUPABASE_URL}/rest/v1/bml_unified_view3" # Updated to your new view name
    headers = {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
    }

    params = {
        "select": "*", # Fetching all for flexibility
        "order": order_string,
        "limit": 100 
    }

    # 4. Apply Filters to Query
    if email_q: params['student_email'] = f"ilike.*{email_q}*"
    if student_q: params['student_name'] = f"ilike.*{student_q}*"
    if grade_q: params['induction_grade'] = f"eq.{grade_q}"

    response = requests.get(url, headers=headers, params=params)
    students = response.json() if response.status_code == 200 else []

    # Map display dates
    for s in students:
        date_val = s.get('induction_submitted_at') or s.get('midterm_submitted_at') or s.get('endterm_submitted_at')
        if date_val:
            try:
                dt = datetime.fromisoformat(date_val.replace('Z', '+00:00'))
                s['display_date'] = dt.strftime('%d/%m/%y %H:%M')
            except:
                s['display_date'] = date_val

    return render(request, "leads/insights_dashboard.html", {
        "students": students,
        "filters": {
            "email": email_q, 
            "student_name": student_q, 
            "grade": grade_q,
            "sort": sort_q
        }
    })
    
    
# views.py

@login_required
def student_dashboard_view(request, email):
    """
    Fetches student data from the unified view using email 
    and renders the student_dashboard.html
    """
    url = f"{settings.SUPABASE_URL}/rest/v1/bml_unified_view3"
    headers = {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
    }
    
    # Filter the view by the student_email column
    params = {
        "student_email": f"eq.{email}",
        "select": "*"
    }
    
    print(f"DEBUG: Loading Profile for {email} from {url}")
    response = requests.get(url, headers=headers, params=params)
    data = response.json()

    if not data or len(data) == 0:
        # Handle case where email doesn't exist in the unified view
        return render(request, "404.html", {"message": "Student record not found"}, status=404)

    # Extract the first matching record
    data.sort(key=lambda x: (x.get('submitted_at') or '', x.get('school') is not None), reverse=True)
    student_data = data[0]

    return render(request, "leads/student_dashboard.html", {
        "student": student_data,
        "email": email
    })