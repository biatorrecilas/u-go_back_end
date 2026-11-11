from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from .models import (
	Badge,
	Course,
	EmailVerificationCode,
	Notification,
	Ride,
	RideBadgeFeedback,
	RideEdit,
	RidePassenger,
	RideRequest,
	RideRequestStatusHistory,
	RideSeat,
	RideSeries,
	RideSeriesWeekday,
	RideStatusHistory,
	University,
	User,
	UserBadge,
	UserProfile,
	UserSession,
	UserTermsAcceptance,
)



class UserCreationAdminForm(forms.ModelForm):
	password1 = forms.CharField(label='Password', widget=forms.PasswordInput)
	password2 = forms.CharField(label='Password confirmation', widget=forms.PasswordInput)

	class Meta:
		model = User
		fields = [
			'email',
			'full_name',
			'phone',
			'ride_visibility_group',
			'university',
			'course',
			'account_status',
			'is_email_verified',
			'is_staff',
			'is_superuser',
		]

	def clean_password2(self):
		password1 = self.cleaned_data.get('password1')
		password2 = self.cleaned_data.get('password2')
		if password1 and password2 and password1 != password2:
			raise forms.ValidationError('Passwords do not match.')
		return password2

	def save(self, commit=True):
		user = super().save(commit=False)
		user.set_password(self.cleaned_data['password1'])
		if commit:
			user.save()
		return user


class UserChangeAdminForm(forms.ModelForm):
	password = ReadOnlyPasswordHashField()

	class Meta:
		model = User
		fields = '__all__'


class UserProfileInline(admin.StackedInline):
	model = UserProfile
	can_delete = False
	extra = 0


@admin.register(User)
class UserAdmin(BaseUserAdmin):
	add_form = UserCreationAdminForm
	form = UserChangeAdminForm
	inlines = [UserProfileInline]

	list_display = (
		'email',
		'full_name',
		'university',
		'course',
		'ride_visibility_group',
		'account_status',
		'is_email_verified',
		'is_staff',
	)
	list_filter = (
		'account_status',
		'is_email_verified',
		'is_staff',
		'is_superuser',
		'ride_visibility_group',
		'university',
	)
	search_fields = ('email', 'full_name', 'phone')
	ordering = ('email',)
	readonly_fields = ('last_login', 'created_at', 'updated_at')

	fieldsets = (
		(None, {'fields': ('email', 'password')}),
		('Personal info', {'fields': ('full_name', 'phone', 'ride_visibility_group')}),
		('Academic info', {'fields': ('university', 'course')}),
		('Permissions', {'fields': ('account_status', 'is_email_verified', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
		('Important dates', {'fields': ('last_login', 'created_at', 'updated_at')}),
	)
	add_fieldsets = (
		(
			None,
			{
				'classes': ('wide',),
				'fields': (
					'email',
					'full_name',
					'phone',
					'ride_visibility_group',
					'university',
					'course',
					'account_status',
					'is_email_verified',
					'is_staff',
					'is_superuser',
					'password1',
					'password2',
				),
			},
		),
	)


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
	list_display = ('name', 'email_domain', 'created_at')
	search_fields = ('name', 'email_domain')


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
	list_display = ('name', 'university', 'is_active', 'created_at')
	list_filter = ('university', 'is_active')
	search_fields = ('name',)


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
	list_display = ('code', 'name', 'badge_kind', 'is_active')
	list_filter = ('badge_kind', 'is_active')
	search_fields = ('code', 'name')


@admin.register(RideSeries)
class RideSeriesAdmin(admin.ModelAdmin):
	list_display = ('id', 'driver_user', 'origin_text', 'destination_text', 'departure_time', 'vehicle_type', 'gender_restriction', 'is_active')
	list_filter = ('vehicle_type', 'gender_restriction', 'is_active')
	search_fields = ('origin_text', 'destination_text', 'driver_user__email', 'driver_user__full_name')


@admin.register(Ride)
class RideAdmin(admin.ModelAdmin):
	list_display = ('id', 'driver_user', 'origin_text', 'destination_text', 'scheduled_start_at', 'status', 'vehicle_type', 'gender_restriction')
	list_filter = ('status', 'vehicle_type', 'gender_restriction')
	search_fields = ('origin_text', 'destination_text', 'driver_user__email', 'driver_user__full_name')


@admin.register(RideSeat)
class RideSeatAdmin(admin.ModelAdmin):
	list_display = ('ride', 'seat_number', 'seat_status', 'occupant_user', 'reserved_for_request')
	list_filter = ('seat_status',)
	search_fields = ('ride__id', 'occupant_user__email', 'occupant_user__full_name')


@admin.register(RideRequest)
class RideRequestAdmin(admin.ModelAdmin):
	list_display = ('ride', 'passenger_user', 'seat_number', 'status', 'requested_at', 'contact_unlocked_at')
	list_filter = ('status',)
	search_fields = ('ride__id', 'passenger_user__email', 'passenger_user__full_name')


@admin.register(RidePassenger)
class RidePassengerAdmin(admin.ModelAdmin):
	list_display = ('ride', 'passenger_user', 'seat_number', 'confirmed_at', 'completed_at')
	search_fields = ('ride__id', 'passenger_user__email', 'passenger_user__full_name')


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
	list_display = ('recipient_user', 'type', 'title', 'is_read', 'created_at')
	list_filter = ('type', 'is_read')
	search_fields = ('recipient_user__email', 'recipient_user__full_name', 'title')


@admin.register(UserBadge)
class UserBadgeAdmin(admin.ModelAdmin):
	list_display = ('user', 'badge', 'progress_value', 'target_value', 'achieved_at')
	search_fields = ('user__email', 'user__full_name', 'badge__name')


@admin.register(RideBadgeFeedback)
class RideBadgeFeedbackAdmin(admin.ModelAdmin):
	list_display = ('ride', 'giver_user', 'receiver_user', 'badge', 'created_at')
	search_fields = ('giver_user__email', 'receiver_user__email', 'badge__name')


admin.site.register(UserProfile)
admin.site.register(UserTermsAcceptance)
admin.site.register(EmailVerificationCode)
admin.site.register(UserSession)
admin.site.register(RideEdit)
admin.site.register(RideSeriesWeekday)
admin.site.register(RideStatusHistory)
admin.site.register(RideRequestStatusHistory)
