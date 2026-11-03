{{/* The image reference: a digest wins over a tag; one of them is required. */}}
{{- define "openplanner.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ required "image.tag (for example 0.4.0) or image.digest is required" .Values.image.tag }}
{{- end -}}
{{- end -}}

{{- define "openplanner.labels" -}}
app.kubernetes.io/name: openplanner
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "openplanner.selector" -}}
app.kubernetes.io/name: openplanner
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* Settings shared by web and worker. */}}
{{- define "openplanner.env" -}}
- name: PUBLIC_BASE_URL
  value: {{ required "publicBaseUrl is required (https://...)" .Values.publicBaseUrl | quote }}
{{- range $name, $value := .Values.env }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- range .Values.secretKeys }}
- name: {{ . }}_FILE
  value: /run/secrets/{{ . }}
{{- end }}
{{- end -}}

{{- define "openplanner.mounts" -}}
- {name: data, mountPath: /srv/data}
- {name: tmp, mountPath: /tmp}
- {name: secrets, mountPath: /run/secrets, readOnly: true}
{{- end -}}

{{- define "openplanner.securityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
capabilities:
  drop: [ALL]
seccompProfile:
  type: RuntimeDefault
{{- end -}}
