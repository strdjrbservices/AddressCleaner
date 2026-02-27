from django.shortcuts import render
from django.http import HttpResponse
from .forms import AddressUploadForm
from .utils import process_data_file

def index(request):
    if request.method == 'POST':
        form = AddressUploadForm(request.POST, request.FILES)
        if form.is_valid():
            file_obj = request.FILES['file']
            street_col = form.cleaned_data['street_col']
            unit_col = form.cleaned_data['unit_col']
            city_col = form.cleaned_data['city_col']
            state_col = form.cleaned_data['state_col']
            zip_col = form.cleaned_data['zip_col']

            try:
                # Process the file
                processed_file = process_data_file(
                    file_obj, street_col, unit_col, city_col, state_col, zip_col
                )

                # Return response as a file download
                response = HttpResponse(processed_file, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                response['Content-Disposition'] = 'attachment; filename="Cleaned_Addresses.xlsx"'
                return response
            
            except Exception as e:
                form.add_error(None, f"Error processing file: {str(e)}")
    else:
        form = AddressUploadForm()

    return render(request, 'address_cleaner/index.html', {'form': form})
