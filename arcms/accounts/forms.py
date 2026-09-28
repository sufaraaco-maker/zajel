from django import forms
from django.contrib.auth.forms import PasswordChangeForm

from .models import User
from .roles import Role


class LoginForm(forms.Form):
    username = forms.CharField(label="اسم المستخدم أو البريد", max_length=150)
    password = forms.CharField(label="كلمة المرور", widget=forms.PasswordInput)


class OTPForm(forms.Form):
    code = forms.CharField(
        label="رمز التحقق",
        max_length=16,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "autofocus": True}),
    )


class EnrollForm(forms.Form):
    current = forms.CharField(
        label="رمز الجهاز الحالي أو رمز استرداد",
        max_length=16,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code"}),
    )
    code = forms.CharField(
        label="الرمز الظاهر في التطبيق",
        max_length=8,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "autofocus": True}),
    )


class PasswordChange(PasswordChangeForm):
    """تتطلب كلمة المرور الحالية: جلسة منسية مفتوحة لا تكفي للاستيلاء على الحساب."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "كلمة المرور الحالية"
        self.fields["new_password1"].label = "كلمة المرور الجديدة"
        self.fields["new_password2"].label = "تأكيد كلمة المرور الجديدة"


class UserForm(forms.ModelForm):
    password = forms.CharField(
        label="كلمة مرور مؤقتة",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="12 حرفاً على الأقل. سيُطلب من المستخدم تغييرها وتفعيل التحقق الثنائي عند أول دخول.",
    )

    class Meta:
        model = User
        fields = ["username", "display_name", "email", "role", "phone", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = Role.choices
        if not self.instance.pk:
            self.fields["password"].required = True

    def clean_password(self):
        from django.contrib.auth.password_validation import validate_password

        pwd = self.cleaned_data.get("password")
        if pwd:
            validate_password(pwd, self.instance)
        return pwd

    def save(self, commit=True):
        user = super().save(commit=False)
        pwd = self.cleaned_data.get("password")
        if pwd:
            user.set_password(pwd)
            user.must_change_password = True
        if commit:
            user.save()
        return user
