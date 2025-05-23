"""
Feature engineering pipeline for datathon-mle.
Loads all paths and parameters from config.yaml for reproducibility.
"""

import pandas as pd
from typing import Any, List
from tqdm import tqdm
from sentence_transformers import SentenceTransformer, util
from rapidfuzz import fuzz
from sklearn.preprocessing import OneHotEncoder
import joblib
import os
from mle_datathon.utils import get_abs_path, load_config, set_log

logger = set_log("feature_engineering")


def clean_features_data(df):
    colunas_remover = [
        "analista_responsavel",
        "cidade",
        "cliente",
        "cod_vaga",
        "codigo",
        "data_candidatura",
        "data_final",
        "data_inicial",
        "data_requicisao",
        "empresa_divisao",
        "estado",
        "limite_esperado_para_contratacao",
        "local_trabalho",
        "nome",
        "recrutador",
        "regiao",
        "requisitante",
        "situacao_candidado",
        "solicitante_cliente",
        "ultima_atualizacao",
    ]
    colunas_remover_2 = [
        "titulo",
        "comentario",
        "titulo_vaga",
        "prazo_contratacao",
        "prioridade_vaga",
        "nivel profissional",
        "nivel_ingles",
        "nivel_espanhol",
        "areas_atuacao",
        "principais_atividades",
        "competencia_tecnicas_e_comportamentais",
        "demais_observacoes",
        "equipamentos_necessarios",
        "habilidades_comportamentais_necessarias",
        "valor_venda",
        "valor_compra_1",
    ]

    colunas_remover.extend(colunas_remover_2)

    df = df.drop(columns=colunas_remover)

    features = [
        col
        for col in df.columns
        if col != "target" and pd.api.types.is_numeric_dtype(df[col])
    ]
    X = df[features]

    return X


def coluna_valida(df: pd.DataFrame, col: str) -> bool:
    """Check if a column is valid for feature creation."""
    if col not in df.columns:
        return False
    missing = df[col].isnull().mean()
    if missing > 0.5:
        logger.info(
            f'[SKIP] Coluna "{col}" com {missing:.0%} missing, feature não criada.'
        )
        return False
    vc = df[col].value_counts(normalize=True, dropna=True)
    if not vc.empty and vc.iloc[0] > 0.9:
        logger.info(
            f'[SKIP] Coluna "{col}" com valor dominante ({vc.index[0]}) em {vc.iloc[0]:.0%}, feature não criada.'
        )
        return False
    return True


def tamanho_texto(texto):
    if not isinstance(texto, str):
        return 0
    return len(texto) if texto else 0


def n_palavras(texto):
    if not isinstance(texto, str):
        return 0
    return len(texto.split()) if texto else 0


def conta_palavras_chave(texto: Any, palavras) -> int:
    if pd.isnull(texto):
        return 0
    texto = str(texto).lower()
    return sum(1 for p in palavras if p in texto)


def conta_cursos(texto):
    if not isinstance(texto, str):
        return 0
    # Look for exact word 'curso' with word boundaries
    palavras = texto.lower().split()
    return sum(1 for palavra in palavras if palavra == "curso")


class TextFeatureGenerator:
    def __init__(self):
        logger.info("[Init] Carregando modelo de embeddings...")
        self.embedding_model = SentenceTransformer(
            "paraphrase-multilingual-MiniLM-L12-v2"
        )

    def tamanho_texto(self, texto: Any) -> int:
        if pd.isnull(texto):
            return 0
        return len(str(texto))

    def n_palavras(self, texto: Any) -> int:
        if pd.isnull(texto):
            return 0
        return len(str(texto).split())

    def gerar_embeddings_agregados(
        self, textos: List[str], batch_size: int = 128
    ) -> pd.DataFrame:
        logger.info(f"[Embeddings] Iniciando geração para {len(textos)} textos...")

        embeddings = self.embedding_model.encode(
            textos,
            batch_size=batch_size,
            show_progress_bar=True,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )

        logger.info("[Embeddings] Geração finalizada. Criando features agregadas...")
        df_emb = pd.DataFrame(
            {
                "emb_mean": embeddings.mean(axis=1),
                "emb_std": embeddings.std(axis=1),
                "emb_min": embeddings.min(axis=1),
                "emb_max": embeddings.max(axis=1),
            }
        )

        return df_emb

    def transform(self, df: pd.DataFrame, campos_texto: List[str]) -> pd.DataFrame:
        for campo in campos_texto:
            if campo in df.columns:
                logger.info(f"[{campo}] Criando features de tamanho e palavras...")
                df[f"{campo}_nchar"] = df[campo].apply(self.tamanho_texto)
                df[f"{campo}_nwords"] = df[campo].apply(self.n_palavras)

                logger.info(f"[{campo}] Criando embeddings agregados...")
                textos = df[campo].fillna("").astype(str).tolist()
                df_emb = self.gerar_embeddings_agregados(textos)
                df_emb.columns = [f"{campo}_{col}" for col in df_emb.columns]

                df = pd.concat([df, df_emb], axis=1)
                logger.info(f"[{campo}] Features de embeddings agregados adicionadas.")

        return df

    def gerar_embeddings(self, textos: pd.Series):
        textos = textos.fillna("").astype(str).tolist()
        return self.embedding_model.encode(
            textos, convert_to_tensor=True, normalize_embeddings=True
        )

    def similaridade_string(self, t1, t2):
        if pd.isnull(t1) or pd.isnull(t2):
            return 0
        return fuzz.token_sort_ratio(str(t1), str(t2)) / 100

    def adicionar_similaridade_titulo_vaga(
        self, df: pd.DataFrame, col1="titulo", col2="titulo_vaga", batch_size=1000
    ) -> pd.DataFrame:
        if col1 in df.columns and col2 in df.columns:
            # String similarity calculation remains the same
            tqdm.pandas(desc="[String Similarity]")
            df["titulo_sim_ratio"] = df.progress_apply(
                lambda row: self.similaridade_string(row[col1], row[col2]), axis=1
            )

            tqdm.write("[Embeddings] Gerando embeddings dos títulos...")
            similarities = []

            for i in tqdm(range(0, len(df), batch_size), desc="Processing batches"):
                batch_df = df.iloc[i : i + batch_size]
                emb1 = self.gerar_embeddings(batch_df[col1].fillna("").astype(str))
                emb2 = self.gerar_embeddings(batch_df[col2].fillna("").astype(str))

                batch_similarities = util.cos_sim(emb1, emb2).diagonal().cpu().numpy()
                similarities.extend(batch_similarities)

            df["sim_titulo_vs_vaga"] = similarities

        return df


def cria_features(df, save_encoders=False, encoders_path=None):
    # Dictionary to store encoders
    encoders = {}

    # 1. Nível acadêmico do candidato (one-hot encoding)
    nivel_acad_encoder = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
    nivel_acad_encoded = nivel_acad_encoder.fit_transform(df[["nivel_academico"]])
    nivel_acad_columns = [
        f"nivel_acad_{cat}" for cat in nivel_acad_encoder.categories_[0]
    ]
    df_nivel_acad = pd.DataFrame(
        nivel_acad_encoded, columns=nivel_acad_columns, index=df.index
    )
    df = pd.concat([df, df_nivel_acad], axis=1)
    encoders["nivel_academico"] = nivel_acad_encoder
    logger.info("[OK] One-hot de nivel_academico criado.")

    # 2. Tipo de contratação da vaga (one-hot encoding)
    tipo_contr_encoder = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
    tipo_contr_encoded = tipo_contr_encoder.fit_transform(df[["tipo_contratacao"]])
    tipo_contr_columns = [
        f"tipo_contr_{cat}" for cat in tipo_contr_encoder.categories_[0]
    ]
    df_tipo_contr = pd.DataFrame(
        tipo_contr_encoded, columns=tipo_contr_columns, index=df.index
    )
    df = pd.concat([df, df_tipo_contr], axis=1)
    encoders["tipo_contratacao"] = tipo_contr_encoder
    logger.info("[OK] One-hot de tipo_contratacao criado.")

    campos_texto = [
        "principais_atividades",
        "competencia_tecnicas_e_comportamentais",
        "demais_observacoes",
        "comentario",
    ]

    feature_generator = TextFeatureGenerator()
    df = feature_generator.transform(df, campos_texto)

    df = feature_generator.adicionar_similaridade_titulo_vaga(df)

    # Save encoders if requested
    if save_encoders and encoders_path:
        os.makedirs(encoders_path, exist_ok=True)
        for name, encoder in encoders.items():
            encoder_file = os.path.join(encoders_path, f"{name}_encoder.joblib")
            joblib.dump(encoder, encoder_file)
            logger.info(f"[OK] Encoder {name} salvo em {encoder_file}")

    return df


def feature_engineering() -> None:
    local_path = os.getcwd()
    config = load_config(local_path)

    paths = config["paths"]
    for k in paths:
        paths[k] = get_abs_path(local_path, paths[k])

    df = pd.read_parquet(paths["dataset_modelagem"])

    # Create encoders directory if it doesn't exist
    encoders_path = os.path.join(
        os.path.dirname(paths["dataset_modelagem"]), "encoders"
    )
    df = cria_features(df, save_encoders=True, encoders_path=encoders_path)

    df = df.drop(
        columns=[
            "titulo",
            "comentario",
            "titulo_vaga",
            "prazo_contratacao",
            "prioridade_vaga",
            "nivel profissional",
            "nivel_ingles",
            "nivel_espanhol",
            "areas_atuacao",
            "principais_atividades",
            "competencia_tecnicas_e_comportamentais",
            "demais_observacoes",
            "equipamentos_necessarios",
            "habilidades_comportamentais_necessarias",
            "valor_venda",
            "valor_compra_1",
        ]
    )

    df.to_parquet(paths["dataset_features"], index=False)
    logger.info(f"Dataset com features salvos em {paths['dataset_features']}")


def transform_new_data(df: pd.DataFrame, encoders_path: str) -> pd.DataFrame:
    """
    Transforma novos dados usando os encoders salvos.

    Args:
        df: DataFrame com os dados a serem transformados
        encoders_path: Caminho para o diretório onde os encoders estão salvos

    Returns:
        DataFrame com as features transformadas
    """
    logger.info("[Inferência] Carregando encoders salvos...")

    # Carrega os encoders salvos
    nivel_acad_encoder = joblib.load(
        os.path.join(encoders_path, "nivel_academico_encoder.joblib")
    )
    tipo_contr_encoder = joblib.load(
        os.path.join(encoders_path, "tipo_contratacao_encoder.joblib")
    )

    # Aplica transformação nos novos dados
    logger.info("[Inferência] Aplicando transformação de nivel_academico...")
    nivel_acad_encoded = nivel_acad_encoder.transform(df[["nivel_academico"]])
    nivel_acad_columns = [
        f"nivel_acad_{cat}" for cat in nivel_acad_encoder.categories_[0]
    ]
    df_nivel_acad = pd.DataFrame(
        nivel_acad_encoded, columns=nivel_acad_columns, index=df.index
    )

    logger.info("[Inferência] Aplicando transformação de tipo_contratacao...")
    tipo_contr_encoded = tipo_contr_encoder.transform(df[["tipo_contratacao"]])
    tipo_contr_columns = [
        f"tipo_contr_{cat}" for cat in tipo_contr_encoder.categories_[0]
    ]
    df_tipo_contr = pd.DataFrame(
        tipo_contr_encoded, columns=tipo_contr_columns, index=df.index
    )

    # Combina com o dataframe original
    df = pd.concat([df, df_nivel_acad, df_tipo_contr], axis=1)

    # Aplica as transformações de texto
    campos_texto = [
        "principais_atividades",
        "competencia_tecnicas_e_comportamentais",
        "demais_observacoes",
        "comentario",
    ]

    feature_generator = TextFeatureGenerator()
    df = feature_generator.transform(df, campos_texto)
    df = feature_generator.adicionar_similaridade_titulo_vaga(df)

    return df
