from django import forms

class AddressUploadForm(forms.Form):
    file = forms.FileField(label="Upload Excel, CSV, or TSV File")
    street_col = forms.CharField(label="Street Address Column Name", initial="Addresses")
    unit_col = forms.CharField(label="Unit/Apt Column Name (Optional)", required=False)
    city_col = forms.CharField(label="City Column Name (Optional)", required=False)
    state_col = forms.CharField(label="State Column Name (Optional)", required=False)
    zip_col = forms.CharField(label="Zip Code Column Name (Optional)", required=False)
    parser_engine = forms.ChoiceField(
        label="Parsing Engine",
        choices=[
            ('usaddress', 'usaddress (Fast, US-focused)'),
            ('pypostal', 'pypostal (High accuracy, International)'),
            ('deepparse', 'Deepparse (Deep Learning, High accuracy, Slower)'),
        ],
        initial='usaddress',
        widget=forms.RadioSelect
    )
